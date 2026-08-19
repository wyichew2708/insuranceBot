"""Answer composition.

Composition never invents a number and never chooses a brand. It selects the
relevant compiled sections, resolves their `{{table:...}}` transclusions with a
deterministic row fetch, lifts each `[src:...]` reference into a typed Claim,
and renders the channel block from `session.channel`. That is the same
separation the compile loop uses (§D.1): facts are fetched, prose is composed.

With a vLLM endpoint configured the prose can be rewritten under guided
decoding — but only from the Facts already assembled here, so the gates hold
either way.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from harness import Channel, ChannelRender, Claim, Figure, GroundedAnswer, Session
from okf.linter import ALLOW_NUMBER, SOURCE_REF_RE
from okf.tables import TOKEN_RE

from api.retrieval import keywords, score_page
from okf import Bundle, Page, PageType, resolve_transclusions

HEADING_RE = re.compile(r"^##\s+(.+)$", re.M)
PROMO_NUMBER_RE = re.compile(r"(?:S?\$\s?\d[\d,]*(?:\.\d+)?)|(?:\b\d+(?:\.\d+)?\s?%)")

NO_ANSWER = (
    "I could not establish that from our approved product pages. "
    "Let me pass you to a colleague who can confirm it."
)


@dataclass
class Selection:
    page: Page
    heading: str
    body: str
    score: float


@dataclass
class Composition:
    answer: GroundedAnswer
    selections: list[Selection] = field(default_factory=list)
    figures_detail: list[dict[str, str]] = field(default_factory=list)


def split_sections(page: Page) -> list[tuple[str, str]]:
    matches = list(HEADING_RE.finditer(page.body))
    if not matches:
        return [("", page.body.strip())]
    out: list[tuple[str, str]] = []
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(page.body)
        out.append((m.group(1).strip(), page.body[m.end() : end].strip()))
    return out


DEFINITION_RE = re.compile(
    r"\b(what (?:does|do|is|are)\b.*\bmean|what is meant by|explain|definition of|meaning of|"
    r"what counts as|define)\b",
    re.IGNORECASE,
)

PROCEDURE_RE = re.compile(
    r"\b(how do i|how can i|how to|what are the steps|steps to|process for|procedure for|"
    r"how do you|go about)\b",
    re.IGNORECASE,
)

QUANTITY_RE = re.compile(
    r"\b(how (?:long|much|many)|limit|cap|excess|amount|threshold|sub-?limit|payout|"
    r"percentage|discount|per item|how far)\b",
    re.IGNORECASE,
)


def select_sections(
    pages: list[Page],
    question: str,
    limit: int = 3,
    idf: dict[str, float] | None = None,
) -> list[Selection]:
    """Relevance is page relevance times section match, where page relevance is the
    *lexical* score only. The alias boost belongs to retrieval — deciding which
    pages to load — not to composition: letting it through means a supporting
    concept page outranks the benefits section that actually holds the figure.
    """
    terms = keywords(question)
    wants_quantity = bool(QUANTITY_RE.search(question))
    wants_definition = bool(DEFINITION_RE.search(question))
    wants_procedure = bool(PROCEDURE_RE.search(question))
    scored: list[Selection] = []

    for page in pages:
        # Navigation pages carry no claims by construction (the linter exempts
        # them from the source-ref rule), so composing from one can only yield
        # an uncitable answer. They stay useful for retrieval, not for prose.
        if page.frontmatter.type == PageType.index_page:
            continue
        # Page and section relevance are ADDED, not multiplied. A page can be
        # certainly right while its body shares none of the customer's words —
        # that is what the authored aliases and the title exist to bridge — and
        # multiplying would zero it out.
        # Bag of words only, deliberately. The phrase and alias evidence that
        # ranks pages in retrieval identifies the *product*; reusing it here
        # would rank a product page above its own benefits page, which is the
        # page that actually holds the figure.
        page_relevance = score_page(page, terms, idf=idf or {})
        page_type = page.frontmatter.type
        # Question type implies page type: a number comes from a benefits page,
        # a definition from a concept page, a procedure from a journey page.
        if wants_definition and page_type == PageType.concept:
            page_relevance += 0.6
        if wants_procedure and page_type == PageType.journey:
            page_relevance += 0.6

        for heading, body in split_sections(page):
            if not body.strip():
                continue
            section_terms = keywords(f"{heading} {body}")
            if not section_terms or not terms:
                continue
            section_relevance = len(terms & section_terms) / len(terms)
            # A heading hit is a strong signal that this is the right section.
            if terms & keywords(heading):
                section_relevance += 0.35
            # A quantitative question is answered by the section that can
            # actually produce the number.
            if wants_quantity and TOKEN_RE.search(body):
                section_relevance += 0.4
            score = page_relevance + section_relevance
            if score > 0:
                scored.append(Selection(page=page, heading=heading, body=body, score=round(score, 3)))

    scored.sort(key=lambda s: (-s.score, s.page.id, s.heading))
    if not scored:
        # Retrieval found pages but no section shared the question's wording.
        # Answering from the best page's opening section beats refusing.
        for page in pages:
            if page.frontmatter.type == PageType.index_page:
                continue
            sections = split_sections(page)
            if sections:
                heading, body = sections[0]
                return [Selection(page=page, heading=heading, body=body, score=0.0)]
        return []
    # Keep only sections close to the best match. A weakly-related section is
    # not free: it dilutes the answer and drags the groundedness score down.
    floor = max(0.2, scored[0].score * 0.72)
    return [s for s in scored if s.score >= floor][:limit]


def _paragraphs(text: str) -> list[str]:
    """Blank-line separated blocks. A claim is a sentence with its reference,
    which routinely wraps across lines."""
    blocks: list[str] = []
    current: list[str] = []
    for line in text.splitlines():
        if line.strip():
            current.append(line.strip())
        elif current:
            blocks.append(" ".join(current))
            current = []
    if current:
        blocks.append(" ".join(current))
    return blocks


def clean_prose(text: str) -> str:
    """Strip machine markup for display; the bindings live on the contract."""
    text = SOURCE_REF_RE.sub("", text)
    text = text.replace(ALLOW_NUMBER, "")
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"[ \t]+", " ", text)
    # Stripping the machine markup leaves gaps before punctuation.
    text = re.sub(r"\s+([.,;:])", r"\1", text)
    text = re.sub(r"\(\s*\)", "", text)
    return "\n".join(line.strip() for line in text.splitlines() if line.strip())


def render_channel(bundle: Bundle, product: Page | None, session: Session) -> ChannelRender:
    """Deterministic. The model never picks the brand (§C.4)."""
    if product is None or not product.frontmatter.channels:
        # No product in play — a contact question, say. The channel page itself
        # carries the binding, and a hotline is a verbatim-only value, so it is
        # substituted from frontmatter rather than quoted out of prose.
        page = bundle.get(session.channel.value) if session.channel != Channel.unknown else None
        if page is not None:
            extra = page.frontmatter.model_extra or {}
            landing, hotline = extra.get("landing"), extra.get("hotline")
            if landing or hotline:
                return ChannelRender(
                    channel=session.channel,
                    brand=str(extra.get("brand") or "") or None,
                    landing=str(landing) if landing else None,
                    hotline=str(hotline) if hotline else None,
                )
        return ChannelRender(channel=session.channel, both_shown=session.channel == Channel.unknown)
    if session.channel == Channel.unknown:
        return ChannelRender(channel=Channel.unknown, both_shown=True)
    for binding in product.frontmatter.channels:
        if binding.ref == session.channel.value:
            return ChannelRender(
                channel=session.channel,
                brand=binding.brand,
                landing=binding.landing,
                hotline=binding.hotline,
            )
    return ChannelRender(channel=session.channel, both_shown=False)


def compose(
    bundle: Bundle,
    pages: list[Page],
    question: str,
    session: Session,
    product: Page | None,
    version: str,
    tier: str,
    advice_required: bool,
    top_score: float,
    idf: dict[str, float] | None = None,
    no_confident_match: bool = False,
) -> Composition:
    selections = select_sections(pages, question, idf=idf)
    if no_confident_match:
        # Nothing cleared the confidence floor and the raw corpus had nothing
        # either. Composing from the least-bad pages is how an assistant
        # invents a product it does not sell (§F.1) — say so and hand off.
        return Composition(
            answer=GroundedAnswer(
                answer=NO_ANSWER,
                handoff=True,
                advice_flag=advice_required,
                confidence=0.0,
                unresolved=["no page matched the question above the confidence floor"],
            )
        )
    if not selections:
        answer = GroundedAnswer(
            answer=NO_ANSWER,
            handoff=True,
            advice_flag=advice_required,
            confidence=0.0,
            unresolved=["no compiled section matched the question"],
        )
        return Composition(answer=answer)

    paragraphs: list[str] = []
    claims: list[Claim] = []
    figures: list[Figure] = []
    figures_detail: list[dict[str, str]] = []
    unresolved: list[str] = []
    product_key = bundle.product_key(product) if product else ""

    for selection in selections:
        resolved = resolve_transclusions(selection.body, bundle.tables, product_key, version, tier)
        for figure in resolved.figures:
            figures.append(
                Figure(
                    label=f"{figure.benefit_code}.{figure.attribute}",
                    text=figure.text,
                    table_row_id=figure.row_id,
                )
            )
            figures_detail.append(
                {
                    "label": f"{figure.benefit_code}.{figure.attribute}",
                    "value": figure.text,
                    "row_id": figure.row_id,
                    "source_ref": figure.source_ref,
                    "page": selection.page.id,
                }
            )
        unresolved.extend(f"{selection.page.id}:{token}" for token in resolved.unresolved)

        # Promotion facts bind to their effective-dated page, not a table row.
        if selection.page.frontmatter.type == PageType.promotion:
            for match in PROMO_NUMBER_RE.finditer(resolved.text):
                figures.append(Figure(label="promotion", text=match.group(), page_ref=selection.page.id))
                figures_detail.append(
                    {
                        "label": "promotion",
                        "value": match.group(),
                        "row_id": "",
                        "source_ref": selection.page.id,
                        "page": selection.page.id,
                    }
                )

        for paragraph in _paragraphs(resolved.text):
            ref = SOURCE_REF_RE.search(paragraph)
            if not ref:
                continue
            locator = ref.group(1) + (f"#{ref.group(2)}" if ref.group(2) else "")
            claims.append(Claim(text=clean_prose(paragraph), source_id=selection.page.id, locator=locator))

        prose = clean_prose(resolved.text)
        if prose:
            paragraphs.append(prose)

    render = render_channel(bundle, product, session)
    body = "\n\n".join(paragraphs)

    if render.both_shown and product is not None and product.frontmatter.channels:
        routes = ", ".join(f"{b.brand}: {b.landing}" for b in product.frontmatter.channels)
        body += f"\n\nYou can buy or ask about this through either route — {routes}."
    elif render.landing:
        body += f"\n\nYou can continue here: {render.landing}"
        if render.hotline:
            body += f" or call {render.hotline}."

    if advice_required:
        body += (
            "\n\nI can only give factual product information, so I'll connect you "
            "with a licensed adviser for a recommendation."
        )

    confidence = min(0.99, 0.45 + 0.5 * top_score) if not unresolved else 0.4
    answer = GroundedAnswer(
        answer=body.strip(),
        claims=claims,
        figures=figures,
        channel_render=render,
        advice_flag=advice_required,
        confidence=round(confidence, 2),
        unresolved=sorted(set(unresolved)),
    )
    return Composition(answer=answer, selections=selections, figures_detail=figures_detail)


def strip_tokens(text: str) -> str:
    return TOKEN_RE.sub("", text)
