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

# The composer must find every span the gate will look for. Two copies of
# this pattern drift, and the drift shows up as a quotation that composes
# unbound and is refused — so there is one, and this is the one.
from harness.gates import NUMERIC_SPAN_RE
from okf.linter import ALLOW_NUMBER, SOURCE_REF_RE
from okf.tables import TOKEN_RE, find_tokens

from api.retrieval import keywords, score_page
from okf import (
    CHANNEL_BY_SLUG,
    Bundle,
    Page,
    PageType,
    Route,
    resolve_channel_tokens,
    resolve_transclusions,
    route_from_page,
    spec_for,
)

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
    benefits: set[str] | None = None,
) -> list[Selection]:
    """Relevance is page relevance times section match, where page relevance is the
    *lexical* score only. The alias boost belongs to retrieval — deciding which
    pages to load — not to composition: letting it through means a supporting
    concept page outranks the benefits section that actually holds the figure.
    """
    terms = keywords(question)
    # Benefit codes the question implied through customer vocabulary — the
    # bridge between "the airline lost my suitcase" and a section headed
    # "Baggage loss". Without it those questions retrieve the right product and
    # then fail to find the right section, which is the commonest way a
    # situational phrasing dies.
    implied = benefits or set()
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
            # A heading hit is a strong signal that this is the right section,
            # and *how much* of the question the heading covers matters. A flat
            # bonus cannot tell "shares one word" from "is the same sentence" —
            # and a published FAQ heading is literally the customer's question,
            # so on those the overlap approaches one. Without the scaling term
            # the compiled FAQ pages were retrieved, admitted, and then never
            # selected, because a product page's opening section outscored the
            # heading that answered the question word for word.
            if implied:
                # Match the transclusion tokens in the body, not the heading.
                # A heading is whatever the compiler called the section — the
                # seed bundle files every figure under "Headline benefits" —
                # whereas `{{table:contents.limit}}` names the benefit exactly,
                # and it marks the section that can actually produce the number.
                present = {benefit for benefit, _ in find_tokens(body)}
                if present & implied:
                    heading_token = re.sub(r"[^a-z0-9]+", "_", heading.lower()).strip("_")
                    # As strong as a heading hit, because that is what it is:
                    # the customer named the benefit, in their own words.
                    section_relevance += 1.0
                    if any(code in heading_token for code in implied):
                        section_relevance += 0.5
            heading_terms = keywords(heading)
            heading_overlap = len(terms & heading_terms) / len(terms) if terms else 0.0
            if heading_overlap:
                section_relevance += 0.35 + 0.9 * heading_overlap
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
    # Quotation markers are how the wiki records that a clause is reproduced
    # rather than written; the customer reads the clause, not the bookkeeping.
    text = re.sub(r"^>\s?", "", text, flags=re.M)
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"[ \t]+", " ", text)
    # Stripping the machine markup leaves gaps before punctuation.
    text = re.sub(r"\s+([.,;:])", r"\1", text)
    text = re.sub(r"\(\s*\)", "", text)
    return "\n".join(line.strip() for line in text.splitlines() if line.strip())


def declared_routes(bundle: Bundle) -> dict[str, Route]:
    """What each channel's own compiled page says about itself. Those pages are
    compiled from the website, so they outrank the registry constants."""
    out: dict[str, Route] = {}
    for slug, channel in CHANNEL_BY_SLUG.items():
        page = bundle.get(channel.value)
        if page is None:
            continue
        route = route_from_page(channel, page.frontmatter.model_extra or {})
        if route is not None:
            out[slug] = route
    return out


def render_channel(bundle: Bundle, product: Page | None, session: Session) -> ChannelRender:
    """Deterministic (§C.4).

    The model never picks the route. There is no brand to pick either: every
    channel sells the same product, so this resolves *where the customer goes
    next*, nothing more. A channel with several front doors renders its primary
    one and carries the rest as equally valid surfaces.
    """
    spec = spec_for(session.channel)
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
                    name=spec.name if spec else None,
                    purchase=str(extra.get("purchase") or "") or None,
                    intermediary=str(extra.get("intermediary") or "") or None,
                    landing=str(landing) if landing else None,
                    hotline=str(hotline) if hotline else None,
                    surfaces=[str(v) for v in (extra.get("surfaces") or [])],
                )
        return ChannelRender(
            channel=session.channel,
            name=spec.name if spec else None,
            all_routes_shown=session.channel == Channel.unknown,
        )
    if session.channel == Channel.unknown:
        return ChannelRender(channel=Channel.unknown, all_routes_shown=True)
    for binding in product.frontmatter.channels:
        if binding.ref == session.channel.value:
            return ChannelRender(
                channel=session.channel,
                name=binding.name,
                purchase=binding.purchase,
                intermediary=spec.intermediary if spec else None,
                landing=binding.landing,
                hotline=binding.hotline,
                surfaces=list(binding.surfaces),
            )
    return ChannelRender(
        channel=session.channel,
        name=spec.name if spec else None,
        purchase=spec.purchase if spec else None,
        intermediary=spec.intermediary if spec else None,
        landing=spec.landing if spec else None,
        hotline=spec.hotline if spec else None,
        all_routes_shown=False,
    )


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
    benefits: set[str] | None = None,
    no_confident_match: bool = False,
) -> Composition:
    selections = select_sections(pages, question, idf=idf, benefits=benefits)
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
    # Set once any channel-variant block rendered a route: the page has already
    # told the customer where to go, so the standing trailer would repeat it.
    routed_in_body = False
    declared = declared_routes(bundle)

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

        # Routes are substituted from the page's own bindings, falling back to
        # the channel registry, and scoped to this session's route (§C.4). The
        # model never sees the token, so it can never invent a contact.
        routed = resolve_channel_tokens(
            resolved.text, session.channel, selection.page.frontmatter.channels, declared
        )
        resolved.text = routed.text
        routed_in_body = routed_in_body or bool(routed.routes)
        unresolved.extend(f"{selection.page.id}:channel:{t}" for t in routed.unresolved)

        # A quoted clause carries the contract's own figures. They are bound
        # by transcription — the wiki reproduced them from a named document
        # and page, and the numeric-binding gate re-reads that document to
        # confirm it. Without this every exclusions and conditions page
        # compiled from a wording would compose an answer the gate then
        # refused, which is a worse failure than not having the page at all.
        for paragraph in _paragraphs(resolved.text):
            if not paragraph.lstrip().startswith(">"):
                continue
            ref = SOURCE_REF_RE.search(paragraph)
            if ref is None or not ref.group(1).startswith("raw/"):
                continue
            locator = ref.group(1) + (f"#{ref.group(2)}" if ref.group(2) else "")
            for match in NUMERIC_SPAN_RE.finditer(SOURCE_REF_RE.sub("", paragraph)):
                figures.append(Figure(label="quotation", text=match.group(), quote_ref=locator))
                figures_detail.append(
                    {
                        "label": "quotation",
                        "value": match.group(),
                        "row_id": "",
                        "source_ref": locator,
                        "page": selection.page.id,
                    }
                )

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

    if routed_in_body:
        # The channel-variant block already rendered this session's route(s).
        pass
    elif render.all_routes_shown and product is not None and product.frontmatter.channels:
        routes = ", ".join(f"{b.name}: {b.landing}" for b in product.frontmatter.channels)
        body += f"\n\nYou can buy or ask about this through any of these routes — {routes}."
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
