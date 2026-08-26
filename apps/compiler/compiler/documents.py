"""Loop 2, tier 1 — the documents that sit above the website (§D.1).

The crawl records PDFs; `crawler.documents` parses them into `raw/wordings/`
and `raw/product-summaries/`. Until this module existed, nothing read them:
the bundle declared those tiers highest-authority and then compiled every page
from marketing HTML, so "what is actually excluded" was answered from a
brochure while the contract sat unread on disk. Measured on the Etiqa/Tiq
corpus: 108 of 108 exclusions pages said the exclusions could not be
extracted, and 0 wiki pages cited a wording.

Turning a PDF back into structure is the whole job here, and it is heuristic
by nature — the extractors emit a wall of hard-wrapped lines with no heading
markup:

* **paragraphs are rebuilt, not trusted.** A line break inside a sentence is
  an artefact of the page width; only a blank line separates paragraphs.
* **running heads and folios are dropped.** `Page 4 of 14` and `V4 | September
  2025` repeat on every page and would otherwise become "facts".
* **headings are inferred from shape.** A lone short title-cased line that
  does not end mid-sentence opens a section; everything until the next such
  line belongs to it.
* **sections are classified, not indexed.** "General Exclusions", "What is not
  covered" and "Exclusions applicable to all sections" are one role, and the
  role — not the insurer's chosen wording — decides which wiki page the text
  lands on.

What this module deliberately does *not* do is decide truth. It reports what a
document says and where it said it; authority ordering and conflict recording
stay in the compiler.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

#: Authority tiers this module reads, highest first. `brochures` is ingested
#: too but stays out: it is marketing with a PDF wrapper, and admitting it
#: would put a flyer's rounding on the same footing as the contract.
TIERS: tuple[str, ...] = ("wordings", "product-summaries")

#: A campaign is not a product. The ingest tiers by filename, and an insurer
#: names both its contracts and its lucky draws "terms and conditions", so
#: ~45 promotional documents arrive filed as wordings. They describe an offer
#: that expires, not cover that binds, and compiling them into product pages
#: is how a chatbot ends up quoting a 2024 spin-and-win as policy terms.
CAMPAIGN_RE = re.compile(
    r"promo|campaign|giveaway|contest|lucky-?draw|spin-and-win|webinar|festival"
    r"|incentive|treats|sign-?up-?gift|world-cup|cny|ndp|piloxing|hajj|cocacola"
    r"|social-contest|holiday-surprise|movie|luckydraw|charge-giveaway",
    re.I,
)

#: Folios, running heads and revision stamps. They survive extraction, repeat
#: on every page, and read like assertions if left in — but the damage they do
#: is worse than a stray sentence. A revision stamp is short and title-cased,
#: so the heading test accepts it, and every page foot then *opens a new
#: section*: on this corpus 110,000 words of one policy contract filed
#: themselves under a heading called "v1.25". Killing them here is what lets a
#: section run across the page breaks it was printed over.
_MONTH = r"jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec"
FOLIO_RE = re.compile(
    r"^(?:page\s+\d+\s*(?:of|/)\s*\d+"
    r"|\d+\s*(?:of|/)\s*\d+"
    # `V4`, `v1.26`, `Ver 4 | Dec 2023`, `Version 10 (January 2026)`
    r"|(?:v|ver|version)\.?\s*:?\s*\d+(?:\.\d+)*\s*(?:[|(\[].*)?"
    # a bare date line: `1 July 2025`, `15 Dec 2020`, `March 2025`
    rf"|(?:\d{{1,2}}\s+)?(?:{_MONTH})[a-z]*\.?\s+\d{{4}}"
    # internal document codes: `CSR/PA/March/2025`
    rf"|[A-Za-z]{{2,5}}(?:/[\w{_MONTH}]+){{2,}}"
    r"|last\s+update[d]?\s*:?.*"
    r"|\d+"
    r")$",
    re.I,
)
PAGE_MARK_RE = re.compile(r"^page\s+(\d+)\s*(?:of|/)\s*\d+$", re.I)

#: markitdown emits extracted tables under a synthetic `## Table N` heading.
#: They are structure, not prose, and are handled by the benefit-table path.
TABLE_HEAD_RE = re.compile(r"^#+\s*table\s+\d+\s*$", re.I)

#: A heading is a *lone* line: short, title-cased or numbered, and not the
#: tail of a sentence. Ten words is generous — "Exclusions applicable to all
#: sections of this policy" is nine.
HEADING_MAX_WORDS = 12
HEADING_MAX_CHARS = 90

#: Leading section numbering: "5.", "Section III -", "Part B)", "(c)".
ENUMERATION_RE = re.compile(
    r"^\(?(?:section|part|clause|appendix)?\s*[\dIVXivx]+[.)\]]?\s*[\u2013\u2014-]?\s*", re.I
)

#: Role vocabulary, ordered — first match wins, so the qualified forms lead.
#: "Benefit Exclusions" is an exclusions section, not a benefits one.
ROLE_RULES: list[tuple[str, re.Pattern[str]]] = [
    # Navigation, not content: a table of contents lists the headings that
    # follow, and compiling it produces a page of orphaned fragments.
    ("contents", re.compile(r"^(?:table\s+of\s+)?contents$|^index$", re.I)),
    (
        "exclusions",
        re.compile(
            r"exclusion|not\s+covered|do\s+not\s+cover|we\s+will\s+not\s+pay"
            r"|excluded|what\s+is\s+not|limitation\s+of\s+(?:cover|liability)",
            re.I,
        ),
    ),
    (
        "definitions",
        re.compile(
            r"definition|glossary|meaning\s+of\s+word|interpretation|words\s+with\s+special"
            # Consumer-drafted contracts ask the question instead of labelling
            # the section: "What do we mean with these words?" is 40,000 words
            # of definitions on this corpus.
            r"|what\s+do\s+we\s+mean|words?\s+(?:we|and\s+phrases)\s+use",
            re.I,
        ),
    ),
    (
        "claims",
        re.compile(r"\bclaim|notification\s+of\s+loss|how\s+to\s+make\s+a|proof\s+of\s+loss", re.I),
    ),
    (
        "eligibility",
        re.compile(r"eligib|who\s+can\s+(?:apply|buy)|entry\s+age|age\s+limit|qualify|qualifying", re.I),
    ),
    (
        "benefits",
        re.compile(
            r"benefit|what\s+is\s+covered|what\s+we\s+cover|scope\s+of\s+cover|coverage"
            r"|sum\s+(?:assured|insured)|schedule\s+of\s+cover|extension"
            r"|^section\s+[\divxlc]+\b|scale\s+of\s+compensation|summary\s+of\s+cover"
            r"|list\s+of\s+critical\s+illness",
            re.I,
        ),
    ),
    (
        "conditions",
        re.compile(
            r"condition|general\s+provision|free[\s-]?look|termination|terminate|premium"
            r"|renewal|renew|cancellation|cancel|governing\s+law|policy\s+owners|protection\s+scheme"
            r"|personal\s+data|grace\s+period|reinstat|surrender|nomination|cooling"
            r"|duty\s+of\s+disclosure|misrepresentation|fraud|arbitration|subrogation"
            r"|rights\s+of\s+third\s+parties|governing|jurisdiction|important\s+not"
            r"|incontestab|taxation|change\s+of\s+address|prohibited\s+person"
            r"|general\s+terms|policy\s+will\s+end|warranty|assignment|notice"
            r"|payment|instal(?:l)?ment|refund|excess|deductible|co-?insurance",
            re.I,
        ),
    ),
]

#: Roles worth a page. Everything else is recorded on the document and left
#: uncompiled — the compiler reports the volume rather than silently dropping.
COMPILED_ROLES: tuple[str, ...] = (
    "exclusions",
    "definitions",
    "benefits",
    "claims",
    "eligibility",
    "conditions",
)

#: Filename noise: the document *kind*, which every wording shares and which
#: therefore carries no signal about which product it governs.
_KIND_TOKENS = re.compile(
    r"^(?:policy|policies|contract|contracts|wording|wordings|terms|conditions|tncs?"
    r"|general|provisions?|product|summary|productsummary|summaries|for|the|and"
    r"|final|clean|onwards|utd|draft|updated|new|copy|insurance)$",
    re.I,
)
#: The kind words again, but glued to the plan name with no separator —
#: `policywordings-eprotect-family`, `businessenterprisesolutionpolicywordings`.
#: Filenames are typed by people, and a missing hyphen should not put
#: "Policywordings Eprotect Family" in front of a customer.
_GLUED_KIND_RE = re.compile(r"(?:policy)?(?:wordings?|contracts?|summary|summaries)|productsummary", re.I)

#: Version and date fragments: `v1-25`, `2023-02`, `28052020`, `1-nov-2020`.
_VERSION_RE = re.compile(
    r"(?:^|-)(?:v\d[\w.]*"
    r"|\d{1,2}[-_]?(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*(?:[-_]?\d{2,4})?"
    r"|(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*[-_]?\d{2,4}"
    r"|\d{5,}"
    r"|20\d\d(?:[-_]\d{2})?"
    r"|\d{1,2}[-_]\d{1,2}"
    r"|\d{2})(?=-|$)",
    re.I,
)
#: Range tails left behind once the dates go: `...-to-`, `-on-and-after-`.
_RANGE_TOKENS = re.compile(r"^(?:to|on|and|after|from|before|till|until|onward|onwards|t)$", re.I)


@dataclass
class DocSection:
    """One classified stretch of a document."""

    heading: str
    role: str
    paragraphs: list[str] = field(default_factory=list)
    #: Printed page the section opens on, where the extractor kept folios.
    page: int | None = None

    @property
    def words(self) -> int:
        return sum(len(p.split()) for p in self.paragraphs)


@dataclass
class Document:
    path: Path
    #: Bundle-relative locator, e.g. `raw/wordings/tiq-home-policy-wording.md`.
    ref: str
    tier: str
    meta: dict[str, str]
    #: Product name inferred from the filename, normalised for matching.
    plan: str
    sections: list[DocSection] = field(default_factory=list)

    @property
    def source_url(self) -> str:
        return self.meta.get("source_url", "")

    @property
    def title(self) -> str:
        return self.meta.get("title", "") or self.plan.replace("-", " ").title()

    def locator(self, section: DocSection) -> str:
        """A citation that points *into* the document, not just at it."""
        return f"{self.ref}#p{section.page}" if section.page else self.ref

    def by_role(self, role: str) -> list[DocSection]:
        return [s for s in self.sections if s.role == role]


def normalise_plan(stem: str) -> str:
    """Filename → the product name it is trying to say.

    `policy-contract-for-early-ci-rider-v1-23` → `early-ci-rider`. Version and
    date fragments go first (they are the reason one plan owns six files),
    then the document-kind words, then the tails those removals strand.
    """
    slug = stem.lower()
    for _ in range(4):  # `...-v1-25-02042026` needs more than one pass
        stripped = _VERSION_RE.sub("", slug)
        if stripped == slug:
            break
        slug = stripped
    tokens = [t for t in slug.split("-") if t]
    tokens = [t for t in tokens if not _KIND_TOKENS.fullmatch(t)]
    # Strip a glued kind word from whatever survived, then drop anything that
    # was nothing else. `businessenterprisesolutionpolicywordings` is a plan
    # name with a suffix, not a plan called that.
    tokens = [t for t in (_GLUED_KIND_RE.sub("", t) for t in tokens) if t]
    while tokens and _RANGE_TOKENS.fullmatch(tokens[-1]):
        tokens.pop()
    while tokens and _RANGE_TOKENS.fullmatch(tokens[0]):
        tokens.pop(0)
    return "-".join(tokens)


def role_for(heading: str) -> str:
    """Both the numbered heading and the bare one are tested.

    Stripping the enumeration is what lets "7. Exclusions" and "Exclusions"
    classify alike — but "Section 4 - Baggage Delay" carries its role *in* the
    enumeration, and testing only the stripped form loses it.
    """
    stripped = ENUMERATION_RE.sub("", heading).strip()
    for role, pattern in ROLE_RULES:
        if pattern.search(stripped) or pattern.search(heading.strip()):
            return role
    return "other"


def _clean(line: str) -> str:
    """Extractors pad glyphs apart to preserve kerning; the padding is not
    content. `Us  /  We  /  Our` and `Us / We / Our` are the same clause."""
    return re.sub(r"[ \t\u00a0\u2009\u202f]+", " ", line).strip()


def looks_like_heading(text: str) -> bool:
    """Shape alone: short, title-cased or explicitly punctuated as a label,
    and not the tail of a sentence."""
    text = re.sub(r"^#+\s*", "", text).strip()
    if not text or len(text) > HEADING_MAX_CHARS:
        return False
    if text.endswith((".", ",", ";")):
        return False
    # A consumer-drafted contract labels its sections with a question:
    # "What do we mean with these words?" opens 40,000 words of definitions on
    # this corpus, and sentence case would otherwise disqualify it.
    if text.endswith((":", "?")):
        return True
    words = text.split()
    if len(words) > HEADING_MAX_WORDS:
        return False
    lettered = [w for w in words if w[:1].isalpha()]
    if not lettered:
        return False  # a lone figure or reference number
    capitalised = sum(1 for w in lettered if w[0].isupper())
    return capitalised / len(lettered) >= 0.6


#: Sentence enders. A heading may only open where a sentence closed — without
#: that guard a short title-cased line *inside* a clause splits the clause.
_CLOSED_RE = re.compile(r"[.:;?!]$")
#: Once a paragraph is this long, a sentence end is a safe place to break it.
#: Only used where the extractor gave us no blank lines to break on.
_PARAGRAPH_WORDS = 40


@dataclass
class _Block:
    heading: bool
    text: str
    page: int | None


def _blocks(body: str) -> list[_Block]:
    """Lines → headings and paragraphs, with the printed page each fell on.

    Blank lines are the paragraph separator *where the extractor kept them*.
    A third of this corpus has none — one PDF backend emits 2,470 lines with
    89 blanks — so the walk cannot depend on them: headings are recognised
    line by line, and a run of prose is closed at a sentence end once it has
    grown past a paragraph's worth of words.
    """
    out: list[_Block] = []
    paragraph: list[str] = []
    page: int | None = None
    start_page: int | None = None
    closed = True  # nothing precedes the first line, so a heading may open

    def flush() -> None:
        nonlocal paragraph, start_page
        if paragraph:
            out.append(_Block(heading=False, text=" ".join(paragraph), page=start_page))
            paragraph = []
            start_page = None

    for raw in body.splitlines():
        line = _clean(raw)
        if not line:
            flush()
            closed = True
            continue
        mark = PAGE_MARK_RE.match(line)
        if mark:
            flush()
            # The folio prints at the foot of the page it numbers, so whatever
            # comes next was printed on the following one.
            page = int(mark.group(1)) + 1
            closed = True
            continue
        if FOLIO_RE.match(line) or TABLE_HEAD_RE.match(line):
            flush()
            closed = True
            continue
        if line.startswith("|"):
            # Extracted table rows. The benefit-table path owns the figures;
            # here the row only tells us the surrounding prose has ended.
            flush()
            closed = True
            continue
        if closed and looks_like_heading(line):
            flush()
            out.append(_Block(heading=True, text=line, page=page))
            closed = True
            continue
        if not paragraph:
            start_page = page
        paragraph.append(line)
        closed = bool(_CLOSED_RE.search(line))
        if closed and sum(len(p.split()) for p in paragraph) >= _PARAGRAPH_WORDS:
            flush()
    flush()
    return out


def is_heading(lines: list[str]) -> bool:
    """Block-level wrapper kept for callers that hold whole blocks."""
    return len(lines) == 1 and looks_like_heading(lines[0])


def segment(body: str) -> list[DocSection]:
    """Split a parsed document into classified sections.

    Text before the first heading is the front matter every insurer opens
    with — the plan provider's address, the protection-scheme notice — and is
    kept under a synthetic heading so it can be classified like the rest.
    """
    sections: list[DocSection] = []
    current = DocSection(heading="Preamble", role="other", page=None)

    for block in _blocks(body):
        if block.heading:
            if current.paragraphs:
                sections.append(current)
            heading = re.sub(r"^#+\s*", "", block.text).strip().rstrip(":")
            current = DocSection(heading=heading, role=role_for(heading), page=block.page)
            continue
        if current.page is None:
            current.page = block.page
        current.paragraphs.append(block.text)

    if current.paragraphs:
        sections.append(current)
    return sections


def parse_document(path: Path, ref: str, tier: str) -> Document:
    text = path.read_text(encoding="utf-8", errors="replace")
    meta: dict[str, str] = {}
    body = text
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            for line in parts[1].splitlines():
                if ":" in line:
                    key, _, value = line.partition(":")
                    meta[key.strip()] = value.strip().strip('"')
            body = parts[2]
    document = Document(path=path, ref=ref, tier=tier, meta=meta, plan=normalise_plan(path.stem))
    document.sections = segment(body)
    return document


def load_documents(source_root: Path, tiers: tuple[str, ...] = TIERS) -> list[Document]:
    """Every product document in the bundle, campaign paperwork excluded."""
    documents: list[Document] = []
    for tier in tiers:
        directory = source_root / "raw" / tier
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.md")):
            if CAMPAIGN_RE.search(path.stem):
                continue
            document = parse_document(path, f"raw/{tier}/{path.name}", tier)
            if document.plan:
                documents.append(document)
    return documents


def campaign_documents(source_root: Path, tiers: tuple[str, ...] = TIERS) -> list[str]:
    """The paperwork `load_documents` refused, by name.

    Reported rather than hidden: a compile that silently drops 26 of 220
    documents reads exactly like one that compiled them all, and the only way
    to notice would be to miss an answer.
    """
    skipped: list[str] = []
    for tier in tiers:
        directory = source_root / "raw" / tier
        if not directory.is_dir():
            continue
        skipped.extend(p.name for p in sorted(directory.glob("*.md")) if CAMPAIGN_RE.search(p.stem))
    return skipped


def _keys(slug: str) -> set[str]:
    """Forms a product may be named by. The shopfront prefix is dropped on
    purpose: `tiq-home` and `home` are one product on two front doors, which
    is the same merge the channel model makes for the websites."""
    tokens = [t for t in slug.split("-") if t]
    forms = {slug}
    for prefix in ("tiq", "etiqa", "eprotect", "direct"):
        if tokens and tokens[0] == prefix:
            forms.add("-".join(tokens[1:]))
    for form in list(forms):
        forms.add(re.sub(r"-(insurance|plan|cover)$", "", form))
    return {f for f in forms if f}


def match_documents(
    documents: list[Document], product_slugs: list[str]
) -> tuple[dict[str, list[Document]], list[Document]]:
    """(documents per product slug, documents matching no known product).

    Exact name wins over containment, and containment is scored by how much of
    the product name the document accounts for. Without that, `home` matches
    `home-renewal-protection-bundle` as readily as `tiq-home` does.
    """
    by_slug: dict[str, list[Document]] = {}
    unmatched: list[Document] = []
    keyed = {slug: _keys(slug) for slug in product_slugs}

    for document in documents:
        forms = _keys(document.plan)
        # *Every* exact match, not the best one. The crawl yields a product
        # page per front door — `home-insurance` from one host and
        # `tiq-home-insurance` from the other are the same policy, which is
        # the whole premise of the channel model — and one contract governs
        # both. Attaching it to the winner alone leaves the other page
        # answering from marketing copy.
        exact = sorted(slug for slug, slug_forms in keyed.items() if forms & slug_forms)
        if exact:
            for slug in exact:
                by_slug.setdefault(slug, []).append(document)
            continue
        # No exact name. Fall back to the single closest, and only if the
        # names substantially account for each other: without the threshold
        # `home` claims `home-renewal-protection-bundle` as readily as
        # `tiq-home` does.
        best: tuple[float, str] | None = None
        for slug, slug_forms in keyed.items():
            overlap = max(
                (
                    min(len(a), len(b)) / max(len(a), len(b))
                    for a in forms
                    for b in slug_forms
                    if a in b or b in a
                ),
                default=0.0,
            )
            if overlap >= 0.6 and (best is None or overlap > best[0]):
                best = (overlap, slug)
        if best is None:
            unmatched.append(document)
        else:
            by_slug.setdefault(best[1], []).append(document)

    for found in by_slug.values():
        # Wordings outrank product summaries, and within a tier the document
        # with the most classified content is the fullest revision.
        found.sort(key=lambda d: (TIERS.index(d.tier), -sum(s.words for s in d.sections)))
    return by_slug, unmatched
