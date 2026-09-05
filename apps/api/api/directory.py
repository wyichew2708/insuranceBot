"""Answering the customer who is shopping rather than asking.

Every other path in this system finds the *best* page and answers from it.
That is right for "what is the excess on my policy" and wrong for "what life
products do you have", where the honest answer is a list and picking a winner
is the failure. Measured on the real bundle before this existed: "what life
products" returned the Products Liability page, having matched on the word
"products"; "looking for ci product" returned an investment-linked plan,
because the tokeniser drops two-letter words and "ci" never reached scoring.

So this is a directory lookup, not a retrieval. It reads the frontmatter every
product page already carries — title, aliases, line of business — and reports
what matches. Deterministic, claim-per-product, and it names what it found
rather than describing one thing it happened to rank first.

What it deliberately does not do is recommend. Listing what exists is product
information; choosing between them is advice, and the advice-boundary gate
owns that line.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from harness import Claim, GroundedAnswer

from okf import Bundle, Page, PageType, Status

#: Words that describe the *shape* of the request rather than the product. A
#: shopper's question is mostly these, and matching on them is what put
#: Products Liability at the top of "what life products".
_NOISE = frozenset(
    [
        "what",
        "which",
        "show",
        "me",
        "list",
        "looking",
        "search",
        "searching",
        "shopping",
        "for",
        "a",
        "an",
        "the",
        "do",
        "does",
        "are",
        "can",
        "have",
        "has",
        "you",
        "your",
        "got",
        "any",
        "other",
        "more",
        "some",
        "available",
        "offer",
        "offers",
        "sell",
        "sells",
        "insure",
        "product",
        "products",
        "plan",
        "plans",
        "policy",
        "policies",
        "insurance",
        "insure",
        "cover",
        "covers",
        "coverage",
        "coverages",
        "option",
        "options",
        "type",
        "types",
        "kind",
        "kinds",
        "sort",
        "sorts",
        "of",
        "is",
        "it",
        "there",
        "i",
        "am",
        "im",
        "like",
        "want",
        "need",
        "buy",
        "get",
        "about",
        "tell",
        "please",
        "hi",
        "hello",
    ]
)

#: Line-of-business roots a customer might name, and the words that mean them.
#: The taxonomy is the compiler's; this is the customer's half of the bridge.
_LINES: dict[str, tuple[str, ...]] = {
    "protection": ("life", "protection", "term", "whole life", "critical illness", "ci", "death"),
    "health-medical": ("health", "medical", "hospital", "surgical", "illness", "shield"),
    "savings-retirement": ("savings", "saving", "endowment", "retirement", "annuity", "legacy"),
    "investments": ("investment", "invest", "ilp", "unit trust", "fund"),
    "motor": ("motor", "car", "vehicle", "motorcycle", "van", "driving"),
    "general": ("general", "home", "travel", "pet", "maid", "personal accident"),
    "business": ("business", "commercial", "sme", "corporate", "employer", "work injury"),
    "premier": ("premier", "prestige"),
}

#: Past this, a list stops being an answer and becomes a directory dump.
MAX_LISTED = 8


@dataclass
class Directory:
    line: str | None
    products: list[Page]

    @property
    def found(self) -> bool:
        return bool(self.products)


def _terms(question: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", (question or "").lower())
    return {w for w in words if w not in _NOISE and len(w) > 1}


def line_asked_for(question: str) -> str | None:
    """Which line of business the shopper named, if any.

    Longest term first, so "critical illness" beats "illness" and lands on
    protection rather than health.
    """
    text = (question or "").lower()
    best: tuple[int, str] | None = None
    for line, terms in _LINES.items():
        for term in terms:
            if re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", text) and (
                best is None or len(term) > best[0]
            ):
                best = (len(term), line)
    return best[1] if best else None


def _is_listable(page: Page) -> bool:
    """A product a customer can be shown: the product page itself, approved,
    not one of its own exclusions/claims/definitions children."""
    fm = page.frontmatter
    return (
        fm.type == PageType.product
        and fm.status == Status.approved
        and page.id.count("/") == 2  # product/<line>/<slug>, not a child page
    )


def _score(page: Page, terms: set[str]) -> float:
    """How well a product's *name* answers the request.

    Name only — not the body. A shopper asking for critical illness cover means
    the products called that, not every wording that mentions the phrase, and
    scoring bodies returns the whole corpus for any common word.
    """
    fm = page.frontmatter
    surface = f"{fm.title} {' '.join(fm.aliases)} {page.id.rsplit('/', 1)[-1].replace('-', ' ')}".lower()
    hits = sum(1 for t in terms if re.search(rf"(?<![a-z0-9]){re.escape(t)}(?![a-z0-9])", surface))
    if not hits:
        return 0.0
    # A short name that matches every term is a better hit than a long one that
    # happens to contain them.
    return hits + (1.0 / (1 + len(surface.split())))


def lookup(bundle: Bundle, question: str) -> Directory:
    """The products this request names, by line of business and by name."""
    line = line_asked_for(question)
    terms = _terms(question)
    listable = [p for p in bundle.pages.values() if _is_listable(p)]

    scored = sorted(
        ((p, _score(p, terms)) for p in listable),
        key=lambda pair: (-pair[1], pair[0].id),
    )
    by_name = [p for p, score in scored if score > 0]

    if line:
        in_line = [p for p in listable if p.frontmatter.line_of_business == line]
        named = [p for p in by_name if p in in_line]
        # Naming a product is not the same request as naming a line. "Show me
        # pet insurance" was answered with Pet Insurance, Dash Pet Plus — and
        # then Accidental Death, Burglary and every other general product,
        # because the line was used to pad the list. Where the customer named
        # something, the things called that *are* the answer.
        if named:
            return Directory(line=line, products=named)
        rest = sorted(in_line, key=lambda p: p.frontmatter.title)
        return Directory(line=line, products=rest)
    return Directory(line=None, products=by_name)


def _sentence(products: list[Page], line: str | None, total: int) -> str:
    """The listing, written without a numeric count.

    A count would be a true fact the bundle computed and an unbound figure to
    the numeric-binding gate, and there is no carve-out narrow enough to admit
    "19 products" that would not also admit a limit. Saying "these" costs the
    customer nothing and keeps the rule absolute.
    """
    names = ", ".join(p.frontmatter.title for p in products)
    where = f" under {line.replace('-', ' and ')}" if line else ""
    lead = (
        f"These are the closest{where} to what you asked for: "
        if total > len(products)
        else f"We have these{where}: "
    )
    tail = " Tell me which one and I'll give you its cover, exclusions or claim steps."
    more = (
        " There are others — name the kind of cover you want and I'll narrow it down."
        if total > len(products)
        else ""
    )
    return f"{lead}{names}.{tail}{more}"


def answer(bundle: Bundle, question: str) -> GroundedAnswer | None:
    """A directory answer, or None when nothing in the corpus matches.

    None is a real outcome and the caller must respect it: a shopper asking
    for a line this insurer does not write should be told so, not handed the
    nearest thing. That refusal is `NO_ANSWER`, which the composer already
    owns.
    """
    directory = lookup(bundle, question)
    if not directory.found:
        return None
    shown = directory.products[:MAX_LISTED]
    # One claim per product listed, so every name in the answer resolves to the
    # page it came from and the reference-integrity gate has something to check.
    claims = [Claim(text=p.frontmatter.title, source_id=p.id, locator=p.id) for p in shown]
    return GroundedAnswer(
        answer=_sentence(shown, directory.line, len(directory.products)),
        claims=claims,
        confidence=1.0,
    )


def lines_overview(bundle: Bundle) -> GroundedAnswer | None:
    """What is sold, by line, when the shopper named no line at all.

    "What insurance products do you offer?" matched no line and fell through
    to a product clarification — a question answered with a question. The
    right reply is the shape of the catalogue: the lines, one product from
    each so every name resolves to a page, and an invitation to pick a line.
    Not `clarifying`: it delivers something, and the customer can act on it.
    """
    roots = [
        page
        for page in bundle.pages.values()
        if page.frontmatter.type == PageType.product and page.id.count("/") == 2
    ]
    if not roots:
        return None
    by_line: dict[str, list[Page]] = {}
    for page in sorted(roots, key=lambda p: p.id):
        by_line.setdefault(page.id.split("/")[1], []).append(page)
    shown: list[Page] = []
    parts: list[str] = []
    for line, pages in by_line.items():
        first = pages[0]
        shown.append(first)
        label = line.replace("-", " and ")
        parts.append(f"{label} (for example {first.frontmatter.title.split(' — ')[0]})")
    # No counts. A bare number in an answer is a figure the numeric-binding
    # gate expects to find in a benefit-table row, and "37 products" is not
    # in one; the first cut of this sentence was blocked for exactly that.
    answer = (
        "We offer insurance across these lines: "
        + "; ".join(parts)
        + ". Tell me the line or the plan you have in mind and I'll give you its detail."
    )
    claims = [Claim(text=p.frontmatter.title, source_id=p.id, locator=p.id) for p in shown]
    return GroundedAnswer(answer=answer, claims=claims, confidence=1.0)
