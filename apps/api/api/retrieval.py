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
import re
from dataclasses import dataclass, field
from pathlib import Path

from harness import Budget, Candidate, LoadedPage, RagHit, Session, Trace

from okf import Bundle, Page, Status

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


def score_page(page: Page, terms: set[str]) -> float:
    """Lexical score over the frontmatter surface — title, aliases, id — plus
    body headings. Deliberately not vector search: at this bundle size grep
    beats embeddings on precision, and it is debuggable (§J.1)."""
    if not terms:
        return 0.0
    fm = page.frontmatter
    surface = " ".join(
        [
            fm.title,
            " ".join(fm.aliases),
            fm.id.replace("/", " "),
            " ".join(re.findall(r"^##\s+(.+)$", page.body, re.M)),
        ]
    )
    surface_terms = keywords(surface)
    if not surface_terms:
        return 0.0
    overlap = terms & surface_terms
    return len(overlap) / len(terms)


def frontmatter_filter(
    bundle: Bundle, question: str, session: Session, trace: Trace, floor: float
) -> list[tuple[Page, float]]:
    """The pre-read filter. Every rejection is recorded with its reason —
    that log is how you discover the taxonomy is wrong (§F.4)."""
    terms = keywords(question)
    alias_hits = set(bundle.resolve_aliases(question))
    trace.entities = sorted(alias_hits)
    admitted: list[tuple[Page, float]] = []

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

        score = score_page(page, terms)
        if page.id in alias_hits:
            score += 0.5  # entity resolution beats bag-of-words
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
    return admitted


def session_jurisdiction(session: Session) -> str:
    return "SG"


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


def needs_rag(
    question: str, admitted: list[tuple[Page, float]], session: Session, confidence_floor: float
) -> str:
    """RAG fallback triggers (§E.1 step 4). Returns the reason, or ''."""
    if not admitted:
        return "no wiki page matched"
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


def rag_search(raw_root: Path, question: str, session: Session, limit: int = 4) -> list[RagHit]:
    """Lexical BM25-style search over raw/ sections. Structure-aware: an
    exclusion is never split from its parent benefit because sections are the
    unit (§E.2). Version-filtered against the customer's in-force policy."""
    terms = keywords(question)
    if not terms or not raw_root.is_dir():
        return []
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
            overlap = terms & body_terms
            if not overlap:
                continue
            score = len(overlap) / len(terms) * (1 + 0.15 * len(overlap))
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
