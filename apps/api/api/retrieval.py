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
from dataclasses import dataclass, field
from pathlib import Path

from harness import Budget, Candidate, Channel, LoadedPage, RagHit, Session, Trace

from okf import Bundle, Page, PageType, Status, term_idf

# The body is corroborating evidence, not the primary signal: frontmatter is
# the curated surface and should still dominate.
BODY_WEIGHT = 0.35
#: The most an alias hit can be worth, earned only by one that resolves to a
#: single page. Scaled down by fan-out from there — see `_alias_bonus`.
ALIAS_BONUS = 0.5

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
    # A resolved product decides the focus outright. Lexical ranking is what
    # got "want to buy cancer insurance" onto the home-insurance FAQ; where
    # something read the question properly, its answer is not one more score
    # to compare.
    focus = focus_override or focus_product(bundle, scored, terms)
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


def focus_product(bundle: Bundle, scored: dict[str, float], terms: set[str] | None = None) -> str | None:
    """The product the question is about: the highest-scoring page that belongs
    to a product family. Returns None when nothing product-shaped matched, so
    concept-only and cross-product questions are left alone.

    Ties are broken by *name*, then by canonical depth, and only then
    alphabetically. That order matters more than it looks. On "cancer
    insurance" the pet-insurance FAQ and the cancer product page scored
    identically — the FAQ mentions the words, the product is called them — and
    the alphabetical tiebreak handed the focus to pet insurance, which then
    excluded the cancer page from retrieval as "a different product". The
    customer named the product; a page that carries that name in its title is
    not equal evidence to a page that mentions it in passing.
    """
    product_keys = known_product_keys(bundle)
    wanted = terms or set()
    best: tuple[float, int, int, str] | None = None
    for page_id, value in scored.items():
        page = bundle.get(page_id)
        if page is None or value <= 0:
            continue
        key = bundle.product_key(page)
        if key not in product_keys:
            continue
        named = len(wanted & keywords(f"{page.frontmatter.title} {' '.join(page.frontmatter.aliases)}"))
        # Depth 2 is the product page itself; its children describe one facet.
        canonical = 1 if page_id.count("/") == 2 else 0
        rank = (value, named, canonical, page_id)
        if best is None or rank[:3] > best[:3] or (rank[:3] == best[:3] and page_id < best[3]):
            best = rank
    if best is None:
        return None
    page = bundle.get(best[3])
    return bundle.product_key(page) if page else None


def _product_root(bundle: Bundle, page_id: str) -> Page | None:
    """The `product/<line>/<slug>` page a child page belongs to.

    `product/general/travel/faq` belongs to `product/general/travel`, which is
    where the typed edges live. Returns None for a page that is already the
    root, or is not a product page at all.
    """
    parts = page_id.split("/")
    if parts[0] != "product" or len(parts) < 4:
        return None
    return bundle.get("/".join(parts[:3]))


def wiki_read(
    bundle: Bundle,
    seeds: list[tuple[Page, float]],
    trace: Trace,
    budget: Budget,
    limit: int,
    today: dt.date | None = None,
) -> list[Page]:
    """Load whole pages, then follow links. Bounded by the page budget, which
    is a defined exit rather than a silent truncation."""
    pages: list[Page] = []
    seen: set[str] = set()

    def take(page: Page, via: str, hop: int) -> bool:
        if page.id in seen or len(pages) >= limit or budget.would_exceed_pages():
            return False
        seen.add(page.id)
        pages.append(page)
        budget.charge_page()
        trace.loaded.append(
            LoadedPage(page_id=page.id, title=page.frontmatter.title, via=via, hop=hop, chars=len(page.body))
        )
        return True

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
    for seed in list(pages):
        owner = _product_root(bundle, seed.id) or seed
        for ref in (owner.frontmatter.links.exclusions, owner.frontmatter.links.benefits):
            if not ref:
                continue
            linked = bundle.get(ref)
            if linked is not None and bundle.retrievable(linked, today or dt.date.today()):
                take(linked, "graph:typed", 1)

    # Graph traversal — deterministic multi-hop.
    for seed in list(pages):
        for hop, neighbour_id in enumerate(bundle.traverse(seed.id, max_pages=limit + 2), start=0):
            if neighbour_id == seed.id:
                continue
            neighbour = bundle.get(neighbour_id)
            if neighbour is None or not bundle.retrievable(neighbour, today or dt.date.today()):
                continue
            take(neighbour, "graph", hop + 1)

    # Spend anything left over on the next-best seeds.
    for page, _score in seeds[seed_limit:]:
        take(page, "filter", 0)
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
) -> list[RagHit]:
    """Lexical BM25-style search over raw/ sections. Structure-aware: an
    exclusion is never split from its parent benefit because sections are the
    unit (§E.2). Version-filtered against the customer's in-force policy.

    Scored by information overlap, not term count: the fallback is the last
    thing standing between a question the corpus cannot answer and an answer
    assembled from whatever shared the most common words with it."""
    terms = keywords(question)
    if not terms or not raw_root.is_dir():
        return []
    weights = idf or {}
    version = session.policy.version if session.policy else None
    hits: list[RagHit] = []

    for path in sorted(raw_root.rglob("*.md")):
        rel = f"raw/{path.relative_to(raw_root)}"
        text = path.read_text(errors="ignore")
        # Historic-version questions must retrieve that version's wording, never
        # a summary of the current one (§E point 2).
        if version and "/wordings/" in rel and version not in path.name:
            continue
        for section, body in _sections(text):
            body_terms = keywords(f"{section} {body}")
            if not body_terms:
                continue
            # The fallback was triggered because this word matched nothing in
            # the wiki. A raw section that does not contain it either is not
            # the answer — it is the nearest neighbour, which is the failure.
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
            hits.append(
                RagHit(
                    source_path=rel,
                    locator=section,
                    score=round(score, 3),
                    excerpt=" ".join(body.split())[:280],
                )
            )
    hits.sort(key=lambda h: -h.score)
    return hits[:limit]


def _sections(text: str) -> list[tuple[str, str]]:
    matches = list(SECTION_RE.finditer(text))
    if not matches:
        return [("", text)]
    out: list[tuple[str, str]] = []
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        out.append((m.group(1).strip(), text[m.end() : end].strip()))
    return out
