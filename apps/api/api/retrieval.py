"""Retrieval routing (§E.1).

    query → frontmatter filter → wiki-first read → structured lookup
          → RAG fallback over raw/ → SOR tools

Graph traversal replaces multi-hop RAG: loading a product page and following
`links.exclusions` guarantees the complete exclusion set, rather than hoping a
retriever surfaces the exclusion chunk — the failure that produces confidently
wrong coverage answers.
"""

from __future__ import annotations

import datetime as dt
import math
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from harness import Budget, Candidate, Channel, LoadedPage, RagHit, Session, Trace
from okf.graph import TYPED, graph_for, plan_for
from okf.names import index_for
from okf.sources import OFFER_QUESTION_RE, may_support, page_type_of_text

from api.vectors import RawHit, VectorHits
from okf import Bundle, Page, PageType, Scope, Status, term_idf

# The body is corroborating evidence, not the primary signal: frontmatter is
# the curated surface and should still dominate.
BODY_WEIGHT = 0.35
#: The most an alias hit can be worth, earned only by one that resolves to a
#: single page. Scaled down by fan-out from there — see `_alias_bonus`.
ALIAS_BONUS = 0.5

#: Words that name the seller, not the product. Both front doors carry the
#: same cover, so neither separates one product from another.
BRAND_WORDS = frozenset({"tiq", "etiqa"})

#: The most a vector hit can add, earned at similarity 1.0 and scaled down to
#: nothing at `vector_floor`. Comparable to FOCUS_PIN on purpose: a page the
#: words missed entirely should be able to clear the confidence floor on a
#: strong similarity, and no more than that.
VECTOR_WEIGHT = 1.0

#: Added to every page of a product something resolved the question to.
#: Enough to clear the confidence floor on its own, because the point is
#: that this product no longer has to win a word-count contest.
FOCUS_PIN = 1.0

# A contiguous phrase match is identity evidence, not vocabulary overlap. Bags
# of words cannot tell "the personal accident limit on Maid Insurance" from a
# question about Personal Accident Insurance — both products own both terms.
# The page whose *title* appears verbatim is the subject; an alias match is
# weaker because aliases are derived, and a long phrase beats a short one.
PHRASE_WEIGHT = 0.35
TITLE_PHRASE_BONUS = 2.0

CLAUSE_LEVEL_RE = re.compile(
    r"\b(section|clause|policy wording|the wording|sub-?section|paragraph|exact wording)\b",
    re.IGNORECASE,
)

STOPWORDS = {
    "the",
    "a",
    "an",
    "is",
    "are",
    "am",
    "do",
    "does",
    "did",
    "i",
    "my",
    "me",
    "we",
    "you",
    "your",
    "it",
    "of",
    "to",
    "in",
    "on",
    "for",
    "with",
    "that",
    "this",
    "and",
    "or",
    "if",
    "what",
    "when",
    "how",
    "can",
    "will",
    "would",
    "be",
    "have",
    "has",
    "was",
    "were",
    "at",
    "by",
    "from",
    "about",
    "there",
    "their",
    "but",
    "not",
    "no",
    "get",
    "got",
}


#: The speech act, not the subject. These are what a customer says *around* the
#: thing they want, and IDF cannot tell the difference: measured on the real
#: corpus `want` scores 0.791 and `cancer` scores 0.408, because a
#: conversational verb is rare in a corpus of contracts. So "want to buy cancer
#: insurance" ranked the home-insurance FAQ first — its headings are full of
#: "I want to buy" — and the page actually called Cancer Insurance came nowhere.
#:
#: Dropped when ranking *products*, kept everywhere else. "buy" says nothing
#: about which plan the customer means and everything about which section of it
#: they want, so removing it outright broke "how do I buy travel insurance",
#: which is found by that very word.
SPEECH_ACT = frozenset(
    [
        "want",
        "wants",
        "wanted",
        "need",
        "needs",
        "needed",
        "looking",
        "look",
        "interested",
        "please",
        "tell",
        "show",
        "give",
        "help",
        "know",
        "find",
        "take",
        "make",
        "let",
        "hi",
        "hello",
        "thanks",
        "thank",
    ]
)


def subject_terms(text: str) -> set[str]:
    """Question terms with the speech act removed, for ranking products.

    Falls back to the full set when nothing else is left: "I need help" is all
    speech act, and ranking against an empty set scores the whole corpus zero.
    """
    terms = keywords(text)
    return (terms - SPEECH_ACT) or terms


def keywords(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in STOPWORDS and len(w) > 2}


@dataclass
class Retrieved:
    pages: list[Page] = field(default_factory=list)
    rag_hits: list[RagHit] = field(default_factory=list)
    rag_reason: str = ""
    product_page: Page | None = None
    top_score: float = 0.0

    @property
    def page_ids(self) -> list[str]:
        return [p.id for p in self.pages]


def benefit_phrases(bundle: Bundle) -> frozenset[str]:
    """Benefit vocabulary, in the words a customer would use. A product whose
    alias happens to be another product's benefit — "personal accident" is both
    a plan and a line on the maid and mobility tables — is weak evidence of
    what the question is about, so those aliases do not get to claim identity.
    A title match still does."""
    return frozenset(row.benefit_code.replace("_", " ") for row in bundle.tables.rows)


def phrase_words(page: Page, question: str) -> int:
    """Word count of the longest title or alias appearing verbatim in the
    question. A one-word match ("excess") is vocabulary; a multi-word one
    ("travel insurance") is identity."""
    haystack = " ".join(question.lower().split())
    best = 0
    for phrase in [page.frontmatter.title, *page.frontmatter.aliases]:
        candidate = " ".join(phrase.lower().split())
        if len(candidate) >= 4 and candidate in haystack:
            best = max(best, len(candidate.split()))
    return best


def named_products(bundle: Bundle, question: str) -> list[str]:
    """Product keys the question names outright, longest name first.

    One reading, from the product-name index (`okf.names`): titles and aliases
    alike, the shopfront's name included, a longer name absorbing the shorter
    one inside it, one product counted once however many of its names the
    customer used. Everything that recognises a name goes through there.
    """
    return [n.key for n in index_for(bundle).named(question)]


def product_family(bundle: Bundle, question: str, chosen: Page) -> list[Page]:
    """Products whose titles all contain the category the customer named —
    chosen first, then the rest — or nothing where the customer named a
    product outright, or where the phrase picks out one title alone."""
    family = index_for(bundle).family(question, bundle=bundle)
    if family is None or chosen.id not in family.members:
        return []
    pages = [bundle.get(m) for m in family.members]
    members = [p for p in pages if p is not None]
    members.sort(key=lambda pg: (pg.id != chosen.id, -len(pg.frontmatter.title)))
    return members


def phrase_score(page: Page, question: str, ambiguous: frozenset[str] = frozenset()) -> float:
    """Length of the longest title/alias phrase occurring verbatim in the
    question, with titles weighted above aliases."""
    haystack = " ".join(question.lower().split())
    best = 0.0
    title = " ".join(page.frontmatter.title.lower().split())
    if len(title) >= 4 and title in haystack:
        best = TITLE_PHRASE_BONUS * len(title.split())
    for alias in page.frontmatter.aliases:
        phrase = " ".join(alias.lower().split())
        if len(phrase) >= 4 and phrase in haystack and phrase not in ambiguous:
            best = max(best, float(len(phrase.split())))
    return best


def _weighted(matched: set[str], terms: set[str], idf: dict[str, float]) -> float:
    """Share of the question's *information* the page accounts for. An unknown
    term counts at full weight in the denominator — a question the corpus has
    no word for should score low, not be normalised away."""
    total = sum(idf.get(term, 1.0) for term in terms)
    if total <= 0:
        return 0.0
    return sum(idf.get(term, 1.0) for term in matched) / total


def score_page(
    page: Page,
    terms: set[str],
    question: str = "",
    ambiguous: frozenset[str] = frozenset(),
    idf: dict[str, float] | None = None,
) -> float:
    """Lexical score over the frontmatter surface — title, aliases, id,
    headings — plus a lighter-weighted pass over the body. Deliberately not
    vector search: at this bundle size grep beats embeddings on precision, and
    it is debuggable (§J.1).

    The body term matters. Benefit vocabulary such as "alternative
    accommodation" lives in the prose, not in the title or the alias list, so a
    frontmatter-only score returns zero for a question that names the benefit
    directly — which is exactly how a customer asks.
    """
    if not terms:
        return 0.0
    fm = page.frontmatter
    headings = " ".join(re.findall(r"^##\s+(.+)$", page.body, re.M))
    surface_terms = keywords(" ".join([fm.title, " ".join(fm.aliases), fm.id.replace("/", " "), headings]))
    body_terms = keywords(page.body)
    if not surface_terms and not body_terms:
        return 0.0
    weights = idf or {}
    surface_score = _weighted(terms & surface_terms, terms, weights)
    body_score = _weighted(terms & body_terms, terms, weights)
    return surface_score + BODY_WEIGHT * body_score + PHRASE_WEIGHT * phrase_score(page, question, ambiguous)


def frontmatter_filter(
    bundle: Bundle,
    question: str,
    session: Session,
    trace: Trace,
    floor: float,
    focus_override: str | None = None,
    confidence_floor: float = 0.45,
    vector: VectorHits | None = None,
    vector_floor: float = 0.55,
) -> list[tuple[Page, float]]:
    """The pre-read filter. Every rejection is recorded with its reason —
    that log is how you discover the taxonomy is wrong (§F.4)."""
    # Ranking *products*, so the speech act is dropped. Composition keeps it:
    # "buy" says nothing about which plan and everything about which section.
    terms = subject_terms(question)
    ambiguous = benefit_phrases(bundle)
    idf = term_idf(bundle)
    alias_hits = set(bundle.resolve_aliases(question))
    trace.entities = sorted(alias_hits)
    # A flat bonus treats "discount" — which the compiler stamped on all 63
    # promotion pages — as evidence as strong as a product's own name. It is
    # not evidence at all: it lifts every one of them at once and separates
    # none. Weight by how much the matching alias narrows the corpus.
    fanout = bundle.alias_fanout(question)
    total_pages = max(2, len(bundle.pages))
    admitted: list[tuple[Page, float]] = []

    def _alias_bonus(page_id: str) -> float:
        n = fanout.get(page_id, 0)
        if not n:
            return 0.0
        return ALIAS_BONUS * math.log(total_pages / n) / math.log(total_pages)

    scored = {
        page.id: score_page(page, terms, question, ambiguous, idf) + _alias_bonus(page.id)
        for page in bundle.pages.values()
    }
    # Dense recall, fused here and nowhere else. This dict is what
    # `focus_candidates`, the seven-clause filter ladder and the rejected-
    # candidate log all read from, so a page lifted by similarity is subject
    # to every one of them. Fused as a bonus in the lexical scale — the same
    # shape as the alias and focus bonuses — rather than replacing the score:
    # a page the words already found keeps what the words gave it, and a page
    # the words missed can rise above the confidence floor on similarity
    # alone, which is the whole point.
    #
    # Two lexical semantics survive on this side. `must_include` is applied
    # by the caller as a post-filter, not here. And the floor: below
    # `vector_floor` a hit is the shape of every document in the bundle —
    # the dense analogue of RAG_FLOOR — and earns nothing.
    if vector is not None and vector.hits:
        for page_id, similarity in vector.by_page.items():
            if similarity < vector_floor or page_id not in scored:
                continue
            lift = VECTOR_WEIGHT * (similarity - vector_floor) / max(1e-6, 1.0 - vector_floor)
            scored[page_id] += lift
    # A resolved product decides the focus outright. Lexical ranking is what
    # got "want to buy cancer insurance" onto the home-insurance FAQ; where
    # something read the question properly, its answer is not one more score
    # to compare.
    candidates = focus_candidates(bundle, scored, terms)
    focus = focus_override or (candidates[0][0] if candidates else None)
    if not focus_override:
        # Only where nothing read the question properly. A resolved product is
        # an answer, not one more score in a tie.
        trace.ambiguous_products = ambiguous_focus(candidates, confidence_floor)
    if focus_override:
        # And its pages score as if they had won on merit, so the confidence
        # floor does not then discard the product we just identified.
        for page in bundle.pages.values():
            if bundle.product_key(page) == focus_override:
                scored[page.id] = max(scored.get(page.id, 0.0), floor) + FOCUS_PIN
    product_keys = known_product_keys(bundle)

    for page in sorted(bundle.pages.values(), key=lambda p: p.id):
        fm = page.frontmatter
        reason = ""
        if fm.jurisdiction != session_jurisdiction(session):
            reason = f"jurisdiction {fm.jurisdiction}"
        elif fm.status != Status.approved:
            reason = f"status {fm.status.value}"
        elif not fm.is_effective_on(session.today):
            reason = "outside effective window"
        elif fm.is_review_overdue(session.today):
            reason = "review overdue — demoted to RAG"
        elif fm.lifecycle.value == "withdrawn":
            reason = "withdrawn"
        elif fm.type is PageType.promotion and not OFFER_QUESTION_RE.search(question or ""):
            # An offer page scores well on the product's own name and says
            # nothing about the cover. It is admitted only for a customer who
            # asked about the offer; a section-level penalty was not enough,
            # since the page still reached composition.
            reason = "promotion — the customer did not ask about an offer"
        elif (
            fm.type is PageType.channel
            and session.channel is not Channel.unknown
            and page.id != session.channel.value
        ):
            # Another route's page. The session already fixes the route, so
            # this page can only describe a way to buy that is not this
            # customer's — its prose mentions the other route by name, which is
            # how a direct customer ends up reading about bank branches.
            reason = f"different channel ({page.id}, session is {session.channel.value})"
        elif focus is not None:
            page_product = bundle.product_key(page)
            if page_product in product_keys and page_product != focus:
                # Another product's page. Loading it pollutes the answer and
                # drags unrelated exclusion requirements into the gates.
                reason = f"different product ({page_product}, focus is {focus})"

        score = scored[page.id]
        if not reason and score < floor:
            reason = f"score {score:.2f} below floor"

        trace.candidates.append(
            Candidate(
                page_id=page.id,
                title=fm.title,
                admitted=not reason,
                reason=reason,
                score=round(score, 3),
            )
        )
        if not reason:
            admitted.append((page, score))

    admitted.sort(key=lambda pair: (-pair[1], pair[0].id))
    ranks = {page.id: position for position, (page, _) in enumerate(admitted, start=1)}
    for candidate in trace.candidates:
        candidate.rank = ranks.get(candidate.page_id)
    return admitted


def session_jurisdiction(session: Session) -> str:
    return "SG"


def known_product_keys(bundle: Bundle) -> set[str]:
    return {
        bundle.product_key(page) for page in bundle.pages.values() if page.frontmatter.type.value == "product"
    }


#: How close the runner-up has to be before the lead stops meaning anything.
#: Measured rather than guessed: "how do i make a claim" puts 99 pages on an
#: identical 1.350 and "what is covered" puts 213, so on the questions people
#: actually ask the gap is routinely exactly zero.
FOCUS_MARGIN = 0.08


def focus_candidates(
    bundle: Bundle, scored: dict[str, float], terms: set[str] | None = None
) -> list[tuple[str, tuple[float, int, int, str]]]:
    """Product keys the question could be about, best first.

    `focus_product` returns only the winner, and the winner of a 99-way tie
    broken alphabetically is not a finding about the question — it is a finding
    about the alphabet. The runner-up is already computed; keeping it is what
    lets the caller notice there was no real contest.
    """
    product_keys = known_product_keys(bundle)
    wanted = terms or set()
    best_by_key: dict[str, tuple[float, int, int, str]] = {}
    for page_id, value in scored.items():
        page = bundle.get(page_id)
        if page is None or value <= 0:
            continue
        key = bundle.product_key(page)
        if key not in product_keys:
            continue
        named = len(wanted & keywords(f"{page.frontmatter.title} {' '.join(page.frontmatter.aliases)}"))
        # The product's own root page, which needs the *type* as well as the
        # depth. `journey/claim/plate-glass` also has two slashes, and
        # `product_key` resolves it to `plate-glass` — a real product — so on
        # slash count alone a claims journey outranked every actual product
        # page and became the focus for the whole corpus. That is how "how do
        # i make a claim" was answered about Plate Glass.
        canonical = 1 if page_id.count("/") == 2 and page.frontmatter.type == PageType.product else 0
        rank = (value, named, canonical, page_id)
        current = best_by_key.get(key)
        if current is None or rank[:3] > current[:3] or (rank[:3] == current[:3] and page_id < current[3]):
            best_by_key[key] = rank
    return sorted(best_by_key.items(), key=lambda kv: (-kv[1][0], -kv[1][1], -kv[1][2], kv[1][3]))


def ambiguous_focus(
    candidates: list[tuple[str, tuple[float, int, int, str]]], floor: float = 0.45
) -> list[str]:
    """Every product key within `FOCUS_MARGIN` of the leader, or [] if one leads.

    A tie only means "several products match" when the tied score is high. Down
    near zero it means the opposite — nothing matches, and the products are
    level because they are all equally irrelevant. "How do I reach the direct
    channel?" ties three products at 0.212 and is not a product question at
    all; "how do i make a claim" ties eighty-seven at 1.350 and is. Asking the
    customer to choose a product for the first would be a worse answer than the
    one it replaced, so the tie has to clear the same floor a confident answer
    would.

    The whole tie is returned, not a display-sized slice of it, because how
    *wide* it is decides what the customer should be asked. Two or three near
    ties are a choice worth offering; eighty-seven means the question named no
    product at all and the honest reply is to ask which one, not to print a
    menu. The caller makes that call — see `api.clarify`.
    """
    if len(candidates) < 2 or candidates[0][1][0] < floor:
        return []
    top = candidates[0][1]
    close = [key for key, rank in candidates if top[0] - rank[0] <= FOCUS_MARGIN]
    # A clear winner on the tiebreaks is not a tie, even at an equal score: a
    # product the customer *named* is not level with one that mentions the word
    # in passing. This is the distinction that stopped "cancer insurance" being
    # answered from the pet-insurance FAQ.
    if len(close) < 2 or candidates[0][1][1] > candidates[1][1][1]:
        return []
    return close


def focus_product(bundle: Bundle, scored: dict[str, float], terms: set[str] | None = None) -> str | None:
    """The product the question is about, or None when nothing product-shaped
    matched — so concept-only and cross-product questions are left alone.

    Delegates to `focus_candidates` so the winner here and the tie set the
    caller inspects can never disagree about who won.

    Ties break by *name*, then canonical depth, then alphabetically, and that
    order matters more than it looks. On "cancer insurance" the pet-insurance
    FAQ and the cancer product page scored identically — the FAQ mentions the
    words, the product is called them — and an alphabetical tiebreak handed the
    focus to pet insurance, which then excluded the cancer page as "a different
    product". A page carrying the name in its title is not equal evidence to
    one that mentions it in passing.
    """
    candidates = focus_candidates(bundle, scored, terms)
    return candidates[0][0] if candidates else None


def product_family_pages(bundle: Bundle, product_page: str) -> list[str]:
    """A product's own child pages, the published FAQ first.

    Read off the graph's containment map rather than guessed from a list of
    suffixes. The FAQ leads because it is the insurer's own short answer to
    the customer's own question — the composer prefers it, and it has to be
    in hand for the gates to hold it as evidence.
    """
    children = graph_for(bundle).children_of(product_page)
    faq = f"{product_page}/faq"
    return ([faq] if faq in children else []) + [c for c in children if c != faq]


#: How many product roots a reverse walk may pull in when nothing
#: product-shaped was loaded, and the widest fan-in it will walk back through.
#:
#: The fan-in is 1, and that is the whole guard. A concept several products
#: depend on says nothing about which one the customer means: `concept/excess`
#: is referenced by home, travel and private car, and walking back from it
#: picked home — first alphabetically — for the bare questions "deductible"
#: and "co-payment", both of which are answered by the definition and neither
#: of which is about home insurance. Two cases, and the honest reading of both
#: is that the reverse edge identified nothing. At a fan-in of one it cannot:
#: there is exactly one product the concept belongs to, and reaching it is the
#: fact, not the alphabet. Same distinction `ambiguous_focus` draws on the
#: lexical side, same reason.
RESCUE_LIMIT = 2
RESCUE_FANIN = 1


def wiki_read(
    bundle: Bundle,
    seeds: list[tuple[Page, float]],
    trace: Trace,
    budget: Budget,
    limit: int,
    today: dt.date | None = None,
    question: str = "",
    scope: Scope | None = None,
) -> list[Page]:
    """Load whole pages, then follow the graph. Bounded by the page budget,
    which is a defined exit rather than a silent truncation.

    Three passes over the graph, in the order their guarantees are worth:
    the product's own typed edges, then a breadth-first walk shaped by what
    the question asked for, then — only where the first two reached no
    product at all — a walk *backwards* along the edges that point at what we
    did load.
    """
    graph = graph_for(bundle)
    # What this question wants read first. An ordering, never a filter, so no
    # phrasing can talk the harness out of the exclusions page (§F.2).
    order = plan_for(question)
    pages: list[Page] = []
    seen: set[str] = set()
    when = today or dt.date.today()

    rejected_by_scope = 0

    def take(page: Page, via: str, hop: int, edge: str = "") -> bool:
        nonlocal rejected_by_scope
        if page.id in seen or len(pages) >= limit or budget.would_exceed_pages():
            return False
        # The scope is the product the customer named. The filter already kept
        # other products' pages out of the seeds; the walk and the reverse
        # rescue could still reach one along a `ref` edge or back from a
        # shared concept. Nothing crosses here.
        if scope is not None and not scope.allows_page(bundle, page):
            rejected_by_scope += 1
            return False
        seen.add(page.id)
        pages.append(page)
        budget.charge_page()
        trace.loaded.append(
            LoadedPage(
                page_id=page.id,
                title=page.frontmatter.title,
                via=via,
                hop=hop,
                chars=len(page.body),
                edge=edge,
            )
        )
        return True

    def retrievable(page_id: str) -> Page | None:
        page = bundle.get(page_id)
        if page is None or not bundle.retrievable(page, when):
            return None
        return page

    # Reserve capacity for traversal: if the highest-scoring seeds fill the
    # budget, the linked exclusion page never loads — and the exclusion set is
    # precisely what graph traversal exists to guarantee (§E.1).
    seed_limit = max(2, limit - 2)
    for page, _score in seeds[:seed_limit]:
        take(page, "filter", 0)

    # Typed edges first. A product page asserts coverage, and the
    # exclusion-completeness gate will refuse to deliver that assertion unless
    # the exclusions page was actually read — so it is not just another
    # neighbour competing for the last slot with a concept page (§E.1, §F.2).
    #
    # The edges are followed from the *product* a page belongs to, not only
    # from the page itself. Only `product/<line>/<slug>` carries `links`; its
    # `/faq`, `/conditions` and `/cover` children carry none. A turn that
    # retrieved the FAQ and not the parent therefore reached no exclusions
    # page at all, asserted coverage from the FAQ, and was refused — three of
    # six refusals in the last simulation, every one of them a product whose
    # exclusions were sitting one hop away and unreachable.
    #
    # All three typed edges, in the question's order. `claims` was in the
    # schema and in the frontmatter and followed by nothing: "how do I claim"
    # reached the claims page only if the generic walk had budget left after
    # the concepts, which on a wordy product it did not.
    typed = tuple(kind for kind in order if kind in TYPED)
    for seed in list(pages):
        owner = graph.owner_of(bundle, seed.id) or seed.id
        for edge in graph.out_edges(owner, typed):
            linked = retrievable(edge.dst)
            if linked is not None:
                take(linked, "graph:typed", 1, edge.kind.value)

    # Graph traversal — deterministic multi-hop, in the question's edge order.
    for seed in list(pages):
        for found in graph.walk(seed.id, order, max_pages=limit + 2):
            neighbour = retrievable(found.page_id)
            if neighbour is not None:
                take(neighbour, "graph", found.hop, found.kind.value)

    # Nothing product-shaped was reached. A question answered entirely from
    # `concept/pre-existing-condition` is a definition, not an answer about
    # cover, and the forward edges cannot get home from there because links
    # run product → concept and never back. Walk them backwards instead —
    # but only from a concept few products depend on, because one they all
    # depend on identifies none of them.
    if not any(page.frontmatter.type is PageType.product for page in pages):
        rescued = 0
        for seed in list(pages):
            if rescued >= RESCUE_LIMIT:
                break
            incoming = graph.in_edges(seed.id)
            if not incoming or len(incoming) > RESCUE_FANIN:
                continue
            for edge in incoming:
                root = retrievable(graph.owner_of(bundle, edge.src) or edge.src)
                if root is None or root.frontmatter.type is not PageType.product:
                    continue
                if take(root, "graph:back", 1, edge.kind.value):
                    rescued += 1
                    break

    # Spend anything left over on the next-best seeds.
    for page, _score in seeds[seed_limit:]:
        take(page, "filter", 0)
    if rejected_by_scope:
        label = scope.describe() if scope else "open"
        trace.note(f"scope {label}: {rejected_by_scope} page(s) outside the product not read")
    return pages


# Reasons that mean "nothing in the wiki was a real match". If the RAG
# fallback then finds nothing either, the turn has no grounding and must be
# handed off rather than composed from the least-bad pages.
NO_MATCH_PREFIXES = ("top score", "no page")


PRODUCT_HEAD_WORDS = {"insurance", "cover", "coverage", "plan", "plans", "policy", "policies", "protection"}


def unsupported_term(bundle: Bundle, question: str, admitted: list[tuple[Page, float]]) -> str:
    """The question names a product line the corpus does not carry.

    Detected structurally rather than by score: a word the corpus has never
    seen, sitting immediately in front of a product head word — "crop
    insurance", "marine hull insurance", "kidnap and ransom cover". Every other
    word in such a question is corpus-wide vocabulary, so a bag-of-words score
    looks respectable while the one word that identifies the product matches
    nothing, and the answer comes from whichever product sorts first.

    A multi-word title or alias appearing verbatim overrides this: the question
    named a product we do carry, whatever else is unfamiliar in it.
    """
    idf = term_idf(bundle)
    tokens = re.findall(r"[a-z0-9]+", question.lower())
    for position, token in enumerate(tokens):
        if len(token) <= 2 or token in STOPWORDS or token in idf:
            continue
        if not any(word in PRODUCT_HEAD_WORDS for word in tokens[position + 1 : position + 3]):
            continue
        if any(phrase_words(page, question) >= 2 for page, _ in admitted[:5]):
            return ""
        return str(token)
    return ""


def needs_rag(
    question: str,
    admitted: list[tuple[Page, float]],
    session: Session,
    confidence_floor: float,
    bundle: Bundle | None = None,
) -> str:
    """RAG fallback triggers (§E.1 step 4). Returns the reason, or ''."""
    if not admitted:
        return "no wiki page matched"
    if bundle is not None and (missing := unsupported_term(bundle, question, admitted)):
        return f"no page mentions {missing!r}"
    if CLAUSE_LEVEL_RE.search(question):
        return "clause-level question"
    if session.policy is not None:
        wiki_versions = {
            page.frontmatter.version_in_force
            for page, _ in admitted
            if page.frontmatter.type.value == "product" and page.frontmatter.version_in_force
        }
        if wiki_versions and session.policy.version not in wiki_versions:
            # The wiki describes what is on sale; this customer holds an older
            # version, so the answer must come from that version's wording.
            return f"historic version {session.policy.version}"
    if admitted[0][1] < confidence_floor:
        return f"top score {admitted[0][1]:.2f} below confidence floor"
    return ""


SECTION_RE = re.compile(r"^##\s+(.+)$", re.M)


# A hit made only of corpus-wide vocabulary ("insurance", "cover") is not
# evidence; it is the shape of every document in the bundle.
RAG_FLOOR = 0.25
# At least one matched term has to be discriminating. Matching only "insurance"
# and "cover" describes every document in the corpus, not this question.
INFORMATIVE_IDF = 0.4


def rag_search(
    raw_root: Path,
    question: str,
    session: Session,
    limit: int = 4,
    idf: dict[str, float] | None = None,
    must_include: str = "",
    dense: list[RawHit] | None = None,
    dense_floor: float = 0.5,
    admit: Callable[[str], bool] | None = None,
) -> list[RagHit]:
    """Search raw/ sections — lexically, and by similarity where an index is
    configured. Structure-aware: an exclusion is never split from its parent
    benefit because sections are the unit (§E.2). Version-filtered against the
    customer's in-force policy.

    Scored by information overlap, not term count: the fallback is the last
    thing standing between a question the corpus cannot answer and an answer
    assembled from whatever shared the most common words with it.

    The dense list arrives unfiltered — see `VectorSearch.search_raw` — and is
    put through `_admissible` here, the same function the lexical pass uses.
    That is the whole safety argument for the layer: a section the words found
    and a section similarity found are admitted by identical rules, so a blog
    post, another version's wording, or a document missing the one word the
    question turned on cannot enter by the dense door having been refused at
    the lexical one.
    """
    terms = keywords(question)
    weights = idf or {}
    version = session.policy.version if session.policy else None
    lexical: list[RagHit] = []

    if terms and raw_root.is_dir():
        for path in sorted(raw_root.rglob("*.md")):
            rel = f"raw/{path.relative_to(raw_root)}"
            # The product scope, before the file is even read. A document
            # tagged to another product is not evidence for this one, however
            # many words it shares with the question.
            if admit is not None and not admit(rel):
                continue
            text = path.read_text(errors="ignore")
            if not _admissible(rel, question, version, page_type=page_type_of_text(text)):
                continue
            for section, body in raw_sections(text):
                body_terms = keywords(f"{section} {body}")
                if not body_terms:
                    continue
                # The fallback was triggered because this word matched nothing
                # in the wiki. A raw section that does not contain it either is
                # not the answer — it is the nearest neighbour, which is the
                # failure.
                if must_include and must_include not in body_terms:
                    continue
                overlap = terms & body_terms
                if not overlap:
                    continue
                if weights and not any(weights.get(t, 1.0) >= INFORMATIVE_IDF for t in overlap):
                    continue
                score = _weighted(overlap, terms, weights) * (1 + 0.15 * len(overlap))
                if score < RAG_FLOOR:
                    continue
                lexical.append(
                    RagHit(
                        source_path=rel,
                        locator=section,
                        score=round(score, 3),
                        excerpt=" ".join(body.split())[:280],
                        found_by="lexical",
                    )
                )
    lexical.sort(key=lambda h: -h.score)

    admitted_dense = _dense_hits(dense or [], question, version, must_include, dense_floor, admit)
    if not admitted_dense:
        # No index, or nothing survived the filters. Byte-for-byte the lexical
        # fallback as it was, scores included — a deployment without pgvector
        # must not be able to tell this code was touched.
        return lexical[:limit]
    return _fuse(lexical, admitted_dense, limit)


def _admissible(rel: str, question: str, version: str | None, page_type: str) -> bool:
    """May this raw document support an answer to this question at all?

    Both halves of the fallback ask exactly this, which is why it is one
    function. The product pages and the documents, never the marketing: 586 of
    the crawled pages are blog posts, and this fallback is the one path by
    which a blog sentence could reach a customer as the answer. And a
    historic-version question must retrieve *that* version's wording, never a
    summary of the current one (§E point 2).
    """
    if not may_support(rel, question, page_type=page_type):
        return False
    return not (version and "/wordings/" in rel and version not in rel.rsplit("/", 1)[-1])


def _dense_hits(
    hits: list[RawHit],
    question: str,
    version: str | None,
    must_include: str,
    floor: float,
    admit: Callable[[str], bool] | None = None,
) -> list[RagHit]:
    """The index's raw hits, filtered by everything the lexical pass filters by.

    `page_type_of_text` is re-run over the indexed content rather than trusted
    from the row: the index can be older than the classifier, and a document
    that has since been recognised as a blog post must stop being citable the
    moment the code says so, not the next time someone runs `make index`.
    """
    out: list[RagHit] = []
    for hit in hits:
        if hit.similarity < floor:
            continue
        if admit is not None and not admit(hit.source_path):
            continue
        if not _admissible(hit.source_path, question, version, page_type=page_type_of_text(hit.content)):
            continue
        if must_include and must_include not in keywords(hit.content):
            continue
        out.append(
            RagHit(
                source_path=hit.source_path,
                locator=hit.heading,
                score=round(hit.similarity, 3),
                excerpt=" ".join(hit.content.split())[:280],
                found_by="dense",
            )
        )
    out.sort(key=lambda h: -h.score)
    return out


#: Reciprocal-rank fusion's damping constant, at its conventional value. It is
#: what stops a single retriever's first place from deciding the fused order on
#: its own: at k=60 the gap between rank 1 and rank 2 is 0.03% of the score, so
#: agreement between the two lists counts for more than either one's confidence
#: — which is the point, because a BM25-ish share-of-information ratio and a
#: cosine similarity are not on the same scale and never will be.
RRF_K = 60


def _fuse(lexical: list[RagHit], dense: list[RagHit], limit: int) -> list[RagHit]:
    """Reciprocal-rank fusion of the two ranked lists.

    Rank, not score: the two numbers are incomparable — `0.62` from the
    lexical pass and `0.62` from a cosine similarity mean nothing to each
    other — and every attempt to weight one against the other is a coefficient
    fitted to whichever suite was open at the time. What both lists genuinely
    agree on is *order*.

    The returned `score` is renormalised so the leader reads 1.0, because the
    raw fused values live near 0.03 and a console column that always shows
    0.03 tells a human nothing. `found_by` carries what the number no longer
    can: which retriever found it, or that both did.
    """
    ranks: dict[tuple[str, str], dict[str, int]] = {}
    seen: dict[tuple[str, str], RagHit] = {}
    for name, hits in (("lexical", lexical), ("dense", dense)):
        for position, hit in enumerate(hits, start=1):
            key = (hit.source_path, hit.locator)
            ranks.setdefault(key, {})[name] = position
            # The lexical excerpt wins a tie: it is cut from the file on disk,
            # where the dense one is cut from whatever was indexed, and the two
            # can differ by a recompile.
            if key not in seen or name == "lexical":
                seen[key] = hit
    fused: list[RagHit] = []
    for key, positions in ranks.items():
        value = sum(1.0 / (RRF_K + rank) for rank in positions.values())
        hit = seen[key]
        found = "both" if len(positions) == 2 else next(iter(positions))
        fused.append(hit.model_copy(update={"score": value, "found_by": found}))
    best = max(h.score for h in fused)
    fused = [h.model_copy(update={"score": round(h.score / best, 3)}) for h in fused]
    fused.sort(key=lambda h: (-h.score, h.source_path, h.locator))
    return fused[:limit]


def raw_sections(text: str) -> list[tuple[str, str]]:
    """Split a raw document at its `##` headings — the unit both halves of the
    fallback search and the unit `scripts/index_pgvector.py` embeds, so the
    index and the query agree on what a section is."""
    matches = list(SECTION_RE.finditer(text))
    if not matches:
        return [("", text)]
    out: list[tuple[str, str]] = []
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        out.append((m.group(1).strip(), text[m.end() : end].strip()))
    return out
