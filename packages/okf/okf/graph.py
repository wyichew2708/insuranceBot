"""The graph layer — what links to what, and how to walk it (§E.1).

Graph traversal is the deterministic replacement for multi-hop RAG: loading a
product page and following `links.exclusions` *guarantees* the complete
exclusion set, where a retriever can only hope to surface the exclusion chunk.
That guarantee is the whole reason the layer exists, and it is why the walk
here is ordered, typed and reproducible rather than "the neighbours, in
whatever order the dict happened to hold them".

Three things live here that the ad-hoc traversal on `Bundle` could not hold:

**Typed edges, kept typed.** `Bundle.neighbours` flattened `links` into a bare
list of page ids, so by the time the harness read it, the exclusions edge and a
passing concept reference were the same thing. The walk could not prefer one,
and the trace could not say which had been followed. Here an edge carries its
kind all the way to `LoadedPage.edge`.

**Containment.** `product/general/travel/faq` belongs to
`product/general/travel`, and nothing in the frontmatter says so — the
relationship is in the id path, and three separate call sites re-derived it by
slicing strings. It is an edge like any other now (`EdgeKind.child`), which is
what lets `owner_of` answer the question every one of those call sites was
really asking.

**Reverse edges.** Links point from the product to the concept it depends on,
never back. A question that lands on `concept/pre-existing-condition` and
nothing else therefore reached no product at all, and the turn was composed
from a definition. The reverse index is how the walk gets home from there —
see `wiki_read`'s rescue, which is the one place it is used and is guarded to
the case where nothing product-shaped was loaded.

The graph is built once per bundle and cached on it, exactly as `term_idf` is:
it is derived from pages already in memory, costs one pass, and is read on
every turn.
"""

from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass
from enum import Enum

from okf.bundle import Bundle
from okf.page import Page, PageType


class EdgeKind(str, Enum):
    """What one page asserts about another.

    Ordered by how much a traversal budget spent on it is worth by default.
    `exclusions` leads because the exclusion-completeness gate will refuse to
    deliver a coverage assertion unless the exclusions page was actually read
    (§F.2) — an edge the harness cannot afford to leave unfollowed is not one
    more neighbour competing for the last slot.
    """

    exclusions = "exclusions"
    benefits = "benefits"
    claims = "claims"
    concept = "concept"
    #: An untyped `links:` key. The schema allows extras (`Links` is
    #: `extra="allow"`), and a compiler that invents an edge should not have
    #: its output silently dropped — but an edge nobody typed is also not
    #: evidence anyone promised, so it walks last.
    ref = "ref"
    #: Containment, derived from the id path rather than from frontmatter:
    #: `product/general/travel` → `product/general/travel/faq`. Walked last of
    #: all, because a product's children are usually already in the seed set —
    #: they scored on the same words the parent did.
    child = "child"


#: Default walk order. Every plan in `plan_for` is a permutation of this with
#: one kind promoted, never a subset: an intent decides what is read *first*,
#: never what is read at all, so no phrasing can talk the harness out of the
#: exclusions page.
DEFAULT_ORDER: tuple[EdgeKind, ...] = (
    EdgeKind.exclusions,
    EdgeKind.benefits,
    EdgeKind.claims,
    EdgeKind.concept,
    EdgeKind.ref,
    EdgeKind.child,
)

#: The three typed edges the compiler writes. `owner_of` + these is what
#: "follow the product's own edges" means.
TYPED: frozenset[EdgeKind] = frozenset({EdgeKind.exclusions, EdgeKind.benefits, EdgeKind.claims})

#: Everything an author actually wrote in `links:`. `child` is excluded
#: because containment is not a link: a page is not "referenced by" its own
#: parent, and a backlink list or a delete guard built from these two together
#: would report every child page as linked-to and refuse to delete any of them.
LINKED: tuple[EdgeKind, ...] = tuple(kind for kind in DEFAULT_ORDER if kind is not EdgeKind.child)

_CLAIM_RE = re.compile(
    r"\b(claim|claims|claiming|reimburse\w*|payout|pay out|submit|lodge|file a|how do i get paid)\b",
    re.IGNORECASE,
)
_EXCLUSION_RE = re.compile(
    r"\b(exclusion\w*|exclude\w*|not covered|isn'?t covered|won'?t (?:cover|pay)|excluded|"
    r"pre-?existing|limitation\w*|restrictions?)\b",
    re.IGNORECASE,
)
_BENEFIT_RE = re.compile(
    r"\b(how much|limit|limits|sub-?limit|amount|maximum|max|cap|sum insured|benefit\w*|payable)\b",
    re.IGNORECASE,
)


def plan_for(question: str) -> tuple[EdgeKind, ...]:
    """The order this question wants its edges followed in.

    A permutation, never a filter — see `DEFAULT_ORDER`. What it buys is the
    last slot in the budget: "how do I claim for a delayed bag" used to spend
    it on a definitions page, because `claims` was typed in the schema, present
    in the frontmatter, and followed by nothing that knew what it was for.
    """
    if _EXCLUSION_RE.search(question):
        first = EdgeKind.exclusions
    elif _CLAIM_RE.search(question):
        first = EdgeKind.claims
    elif _BENEFIT_RE.search(question):
        first = EdgeKind.benefits
    else:
        return DEFAULT_ORDER
    return (first, *(kind for kind in DEFAULT_ORDER if kind is not first))


@dataclass(frozen=True)
class Edge:
    src: str
    dst: str
    kind: EdgeKind


@dataclass(frozen=True)
class Hop:
    """One page the walk reached, and how it got there."""

    page_id: str
    kind: EdgeKind
    hop: int


class PageGraph:
    """Forward and reverse adjacency over a bundle's pages.

    Both directions are built in the same pass and neither is lazy: the
    reverse index is the smaller half of a structure that is already one dict
    per page, and a walk that has to decide whether to pay for it is a walk
    that will decide wrong on the turn that needed it.
    """

    def __init__(
        self,
        out: dict[str, list[Edge]],
        into: dict[str, list[Edge]],
        parent: dict[str, str],
        children: dict[str, list[str]],
    ) -> None:
        self._out = out
        self._in = into
        # Containment is a hierarchy, not a link, and it is kept as one. It is
        # *also* published as an `EdgeKind.child` edge so the walk can reach a
        # `/cover` page nothing in the frontmatter points at — but that edge is
        # deduped against the typed links, and `product/general/travel` links
        # its own benefits page under `links.benefits`, so the child edge for
        # that pair does not survive. Reading parentage off the edges therefore
        # answered "no parent" for exactly the pages that have the strongest
        # one. These two maps are not deduped against anything.
        self._parent = parent
        self._children = children

    # --- construction -----------------------------------------------------

    @classmethod
    def build(cls, bundle: Bundle) -> PageGraph:
        pages = bundle.pages
        edges: dict[tuple[str, str], Edge] = {}

        def add(src: str, dst: str, kind: EdgeKind) -> None:
            # A page linking to itself is not a hop, and the same pair typed
            # twice keeps the stronger kind: `DEFAULT_ORDER` is that ranking,
            # so a page listed under both `exclusions` and `concepts` is an
            # exclusions edge and traverses like one.
            if src == dst or dst not in pages:
                return
            current = edges.get((src, dst))
            if current is None or DEFAULT_ORDER.index(kind) < DEFAULT_ORDER.index(current.kind):
                edges[(src, dst)] = Edge(src=src, dst=dst, kind=kind)

        # Containment first, because the `child` edges below are read off it:
        # the id path is the only place a bundle records that
        # `product/general/travel/faq` belongs to `product/general/travel`.
        # Only the nearest existing ancestor, so a bundle that grows a level
        # does not suddenly give the root an edge to every descendant.
        parent: dict[str, str] = {}
        children: dict[str, list[str]] = {}
        for page_id in sorted(pages):
            ancestor = _nearest_ancestor(pages, page_id)
            if ancestor is not None:
                parent[page_id] = ancestor
                children.setdefault(ancestor, []).append(page_id)

        for page in pages.values():
            links = page.frontmatter.links
            for ref, kind in (
                (links.exclusions, EdgeKind.exclusions),
                (links.benefits, EdgeKind.benefits),
                (links.claims, EdgeKind.claims),
            ):
                if ref:
                    add(page.id, ref, kind)
            for ref in links.concepts:
                add(page.id, ref, EdgeKind.concept)
            for key, value in (links.model_extra or {}).items():
                if key == "concepts":
                    continue
                if isinstance(value, str):
                    add(page.id, value, EdgeKind.ref)
                elif isinstance(value, list):
                    for item in value:
                        if isinstance(item, str):
                            add(page.id, item, EdgeKind.ref)
        for child_id, ancestor in parent.items():
            add(ancestor, child_id, EdgeKind.child)

        out: dict[str, list[Edge]] = {}
        into: dict[str, list[Edge]] = {}
        for edge in edges.values():
            out.setdefault(edge.src, []).append(edge)
            into.setdefault(edge.dst, []).append(edge)
        for table in (out, into):
            for key in table:
                table[key].sort(key=_edge_order)
        return cls(out, into, parent, children)

    # --- queries ----------------------------------------------------------

    def out_edges(self, page_id: str, kinds: tuple[EdgeKind, ...] | None = None) -> list[Edge]:
        """Edges leaving `page_id`, in `kinds` order where one is given."""
        return _ordered(self._out.get(page_id, []), kinds)

    def in_edges(self, page_id: str, kinds: tuple[EdgeKind, ...] | None = None) -> list[Edge]:
        """Edges arriving at `page_id` — who points here, and as what.

        The half `Bundle` never had. Links run from the product to the concept
        it depends on and never back, so `cms` and `store` each computed
        backlinks by walking all 301 pages' neighbour lists per request; and a
        turn that landed on a concept page and nothing else could not get to a
        product at all.
        """
        return _ordered(self._in.get(page_id, []), kinds)

    def neighbours(self, page_id: str, kinds: tuple[EdgeKind, ...] | None = None) -> list[str]:
        return [edge.dst for edge in self.out_edges(page_id, kinds)]

    def owner_of(self, bundle: Bundle, page_id: str) -> str | None:
        """The product page `page_id` hangs off, or None if it is one itself.

        `product/general/travel/faq` belongs to `product/general/travel`, which
        is where the typed edges live: its `/faq`, `/conditions` and `/cover`
        children carry none. A turn that retrieved the FAQ and not the parent
        therefore reached no exclusions page at all, asserted coverage from the
        FAQ, and was refused.

        Climbs the containment chain rather than stopping at the first
        ancestor, so a bundle that grows a level under a product still resolves
        to the product.
        """
        current = self._parent.get(page_id)
        while current is not None:
            page = bundle.get(current)
            if page is not None and page.frontmatter.type is PageType.product:
                return current
            current = self._parent.get(current)
        return None

    def children_of(self, page_id: str) -> list[str]:
        """Pages contained by `page_id`, alphabetically.

        Read from the containment map rather than from `EdgeKind.child`
        edges: a child the frontmatter *also* links typed — a product's own
        `/benefits` page — is deduped to the stronger kind in the edge set and
        would be missing from a family assembled out of it. Containment is not
        something a link can take away.
        """
        return list(self._children.get(page_id, []))

    def walk(
        self,
        start: str,
        order: tuple[EdgeKind, ...] = DEFAULT_ORDER,
        max_pages: int = 6,
        seen: set[str] | None = None,
    ) -> list[Hop]:
        """Breadth-first, kind-ordered traversal from `start`.

        Deterministic twice over: the frontier is a queue, and each page's
        edges are expanded in `order`, ties broken by page id. Two runs over
        the same bundle produce the same list, which is what makes a trace
        worth reading (§F.4).

        `start` is not yielded — the caller already has it — and `seen` lets a
        caller share one visited set across several starts, so the second seed
        does not re-walk what the first already covered.
        """
        visited = seen if seen is not None else set()
        visited.add(start)
        found: list[Hop] = []
        queue: deque[tuple[str, int]] = deque([(start, 0)])
        while queue and len(found) < max_pages:
            current, depth = queue.popleft()
            for edge in self.out_edges(current, order):
                if edge.dst in visited:
                    continue
                visited.add(edge.dst)
                found.append(Hop(page_id=edge.dst, kind=edge.kind, hop=depth + 1))
                queue.append((edge.dst, depth + 1))
                if len(found) >= max_pages:
                    break
        return found

    def describe(self, page_id: str) -> dict[str, list[str]]:
        """In and out edges as `kind:page_id` strings — for the trace, the
        console and the linter, none of which should be re-deriving this."""
        return {
            "out": [f"{e.kind.value}:{e.dst}" for e in self.out_edges(page_id)],
            "in": [f"{e.kind.value}:{e.src}" for e in self.in_edges(page_id)],
        }

    @property
    def edge_count(self) -> int:
        return sum(len(edges) for edges in self._out.values())


def _edge_order(edge: Edge) -> tuple[int, str, str]:
    """Kind first, then the *other* end of the edge.

    One key serves both directions: an out-edge list shares a `src`, so it
    orders by `dst`; an in-edge list shares a `dst`, so the `dst` term is
    constant and it orders by `src`. Both are alphabetical and neither depends
    on the order pages happened to load in, which is what a reproducible trace
    needs.
    """
    return (DEFAULT_ORDER.index(edge.kind), edge.dst, edge.src)


def _ordered(edges: list[Edge], kinds: tuple[EdgeKind, ...] | None) -> list[Edge]:
    if kinds is None:
        return list(edges)
    rank = {kind: position for position, kind in enumerate(kinds)}
    return sorted(
        (e for e in edges if e.kind in rank),
        key=lambda e: (rank[e.kind], e.dst, e.src),
    )


def _nearest_ancestor(pages: dict[str, Page], page_id: str) -> str | None:
    parts = page_id.split("/")
    for cut in range(len(parts) - 1, 0, -1):
        ancestor = "/".join(parts[:cut])
        if ancestor in pages:
            return ancestor
    return None


def graph_for(bundle: Bundle) -> PageGraph:
    """The bundle's graph, built once. Cached on the bundle like `term_idf`."""
    cached = getattr(bundle, "_graph", None)
    if cached is None:
        cached = PageGraph.build(bundle)
        bundle._graph = cached
    return cached
