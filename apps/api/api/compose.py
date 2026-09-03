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
from harness.ask import Ask, ask_about, asked_benefits
from harness.gates import NUMERIC_SPAN_RE
from harness.intent import REQUIREMENTS, Intent, classify
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

#: Anything that makes a turn a question rather than a name. A turn with
#: none of these is a noun phrase the customer wants explained.
NAMES_ONLY_RE = re.compile(
    r"\b(?:what|which|who|when|where|why|how|is|are|do|does|did|can|could|should|would|will"
    r"|tell|show|explain|list|need|want|got|have|has)\b|\?",
    re.I,
)
PROMO_NUMBER_RE = re.compile(r"(?:S?\$\s?\d[\d,]*(?:\.\d+)?)|(?:\b\d+(?:\.\d+)?\s?%)")

NO_ANSWER = (
    "I could not establish that from our approved product pages. "
    "Let me pass you to a colleague who can confirm it."
)

#: What a refusal can say instead, per intent, when the product is known.
#:
#: A customer told *why* can act. A customer told "I could not establish that"
#: can only ask again, differently, and get the same sentence — which is the
#: refusal this system gives most often. Each of these is true of this corpus:
#: no premium is published anywhere in it, no document is downloadable through
#: it, and a limit genuinely does depend on a plan tier the anonymous session
#: does not know.
SHORTFALL: dict[Intent, str] = {
    Intent.price: (
        "I do not have premiums for {product} — they are not published in the "
        "product documents I answer from, and a price depends on your details. "
        "You will see one when you start an application."
    ),
    Intent.document: (
        "I cannot send documents. The policy wording and product summary for "
        "{product} are published on the product page, and a colleague can send "
        "your own policy documents."
    ),
    Intent.limit: (
        "The limits for {product} vary by plan tier, and I do not know yours. "
        "Sign in or tell me your tier and I can give you the exact figure."
    ),
    Intent.eligibility: (
        "I do not have the eligibility rules for {product} in the documents I "
        "answer from. A colleague can confirm whether you qualify."
    ),
    Intent.claim: ("I do not have the claim steps for {product}. A colleague can take you through it."),
    Intent.application: (
        "I do not have the application steps for {product}. The product page "
        "has the route to buy, and a colleague can take you through it."
    ),
}


def shortfall(question: str, product: Page | None) -> str:
    """The most specific refusal this turn can honestly give.

    Falls back to the generic one where the intent is unrecognised or no
    product was resolved — saying something precise and wrong is worse than
    saying something vague and true.
    """
    if product is None:
        return NO_ANSWER
    template = SHORTFALL.get(classify(question))
    if template is None:
        return NO_ANSWER
    # The product's own name, never a child page's. `_product_page` can settle
    # on `.../conditions`, whose title is "Public liability — Policy
    # conditions" — which is not a product anybody bought.
    name = product.frontmatter.title.split(" — ")[0]
    return template.format(product=name)


def product_name(page: Page) -> str:
    """The product's own name, never a child page's heading."""
    return page.frontmatter.title.split(" — ")[0]


@dataclass
class Selection:
    page: Page
    heading: str
    body: str
    score: float
    #: The section that carries the answer to a procedural question — "How to
    #: buy" for an application, the claims steps for a claim. Kept apart from
    #: `score` on purpose: the cutoff below is *relative* to the best score, so
    #: boosting this section's score raised the bar and cut every other
    #: section loose, leaving an answer with no citable claim at all.
    lead: bool = False


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


#: Enough to put a promotion below the product's own pages without removing it
#: from the answer entirely — a customer asking about cover may still like to
#: know an offer exists, at the end.
PROMOTION_PENALTY = 1.2

#: The sections that describe a product rather than one corner of it.
OVERVIEW_HEADING_RE = re.compile(
    r"^(?:what it covers|what this plan is|about\b|overview|headline benefits|summary"
    r"|key benefits|at a glance)",
    re.I,
)

#: A question that is actually about an offer. Only then does a promotion lead.
OFFER_RE = re.compile(r"\b(promo|promotion|discount|offer|deal|voucher|cashback|rebate|sale)\b", re.I)

#: How much of one section can reach the answer. The median section is 531
#: characters and the 90th percentile 1,545, but "Family Plan" on the travel
#: cover page is 14,708 and the corpus maximum is 53,480 — a definitions dump
#: the segmenter filed under a benefit heading. One of those produced a
#: 1,927-word answer carrying 53 claims, and at that size the rewrite cannot
#: keep every figure, so `Draft.accepts` rejected it and the raw concatenation
#: shipped: the longer the answer, the more certain it stays unreadable.
#: Longer sections are cut at a paragraph boundary — the customer gets the
#: opening, which is where a wording states its rule, and the page is cited so
#: nothing is concealed.
SECTION_BODY_CHARS = 1800


def _capped(body: str) -> str:
    """The opening of a section, whole paragraphs only."""
    if len(body) <= SECTION_BODY_CHARS:
        return body
    kept: list[str] = []
    total = 0
    for paragraph in re.split(r"\n\s*\n", body):
        if total + len(paragraph) > SECTION_BODY_CHARS and kept:
            break
        kept.append(paragraph)
        total += len(paragraph)
    return "\n\n".join(kept) if kept else body[:SECTION_BODY_CHARS]


#: The heading that carries the answer to a procedural question. A customer
#: asking how to buy wants the buying section, not the best-scoring one.
PROCEDURAL_HEADINGS: dict[Intent, re.Pattern[str]] = {
    Intent.application: re.compile(r"how to buy|buy|purchase|apply|get a quote|where to buy", re.I),
    Intent.claim: re.compile(r"claim|how to make|notify|report a", re.I),
}


#: How much a page the requirement asked for outranks one that merely scored.
#: Large enough to beat a lexical near-miss, and still additive, so a named
#: page with nothing relevant in it does not win by fiat.
EVIDENCE_BONUS = 1.5


def evidence_pages(intent: Intent, product: Page | None, loaded: set[str]) -> frozenset[str]:
    """The page ids this intent's requirement says hold the answer.

    Empty when the intent is unconstrained or no product is in hand, which
    leaves selection exactly as it was — this can steer, never starve.
    """
    requirement = REQUIREMENTS.get(intent)
    if requirement is None or product is None or not requirement.holds_answer:
        return frozenset()
    root = product.id
    named = {f"{root}{suffix}" for suffix in requirement.holds_answer}
    # Only pages that were actually loaded. A suffix the bundle does not carry
    # — the seed bundle has no `/cover` child — must not steer anything.
    return frozenset(named & loaded)


def evidence_types(intent: Intent) -> frozenset[str]:
    """Page *types* the requirement accepts, as a second axis.

    "How do I buy this through an agency?" is answered by a channel page,
    which does not live under the product root — so suffixes alone cannot
    name it, and boosting only the product page pushed the channel page out.
    """
    requirement = REQUIREMENTS.get(intent)
    return frozenset(requirement.needs_page_type) if requirement else frozenset()


def select_sections(
    pages: list[Page],
    question: str,
    limit: int = 3,
    idf: dict[str, float] | None = None,
    benefits: set[str] | None = None,
    product: Page | None = None,
    ask: Ask | None = None,
) -> list[Selection]:
    """Relevance is page relevance times section match, where page relevance is the
    *lexical* score only. The alias boost belongs to retrieval — deciding which
    pages to load — not to composition: letting it through means a supporting
    concept page outranks the benefits section that actually holds the figure.
    """
    terms = keywords(question)
    # One reading of the question. The pipeline makes it before retrieval and
    # passes it in; a caller without one gets the light form, which knows the
    # product page and nothing of the rest of the catalogue.
    ask = ask or ask_about(question, product, benefits)
    intent = ask.intent
    # A turn that is just a product name — "term life" — asks nothing, so no
    # intent fires and every child page competes on lexical overlap alone. The
    # exclusions page is the wordier of them and won: typing a product's name
    # was answered with its suicide clause. Naming a product is a request to be
    # told what it is.
    names_only = intent is Intent.unknown and not NAMES_ONLY_RE.search(question)
    # "What does this cover?" wants the cover page. "Are wear and tear
    # covered?" classifies the same way and wants the *exclusions* page — it
    # names a subject and asks whether it is in or out. Steering every
    # coverage question away from exclusions broke six of those, so only the
    # open form is steered.
    # The Ask decides whether the customer wants the shape of the product or
    # one corner of it — see `harness.ask._scope` for the three forms.
    broad_coverage = ask.scope == "overview"
    asks_about_offer = bool(OFFER_RE.search(question))
    # The pages this intent's requirement says hold the answer, resolved
    # against the product in hand. This is the one place the requirement table
    # is read *before* an answer exists rather than after it — everything else
    # consults it to reject. Without it "how to buy" was answered from three
    # FAQ entries that repeat the word "buy", while the product's own "How to
    # buy" section sat unread on a page that was already loaded.
    answer_pages = evidence_pages(intent, product, {p.id for p in pages})
    answer_types = evidence_types(intent)
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
        # A page the requirement named outranks a page that merely shares words
        # with the question. A bonus and never a penalty: docking everything
        # else starved the benefits page on any coverage question and took
        # about a hundred generated cases with it. Steering is adding weight to
        # the right evidence, not removing it from the rest.
        if page.id in answer_pages or page.frontmatter.type.value in answer_types:
            page_relevance += EVIDENCE_BONUS
        # An offer is not cover. "Tiq travel insurance coverage" opened with
        # "55% off Single trip, 30% off Annual multi-trip" because a promotion
        # page outscored the product's own benefits. A promotion answers a
        # question about a promotion and leads nothing else.
        if page_type == PageType.promotion and not asks_about_offer:
            page_relevance -= PROMOTION_PENALTY
        if names_only:
            # The product page itself, not one of its children.
            page_relevance += 0.9 if page.id.count("/") == 2 else -0.4
        # What is covered and what is not are opposite questions answered by
        # adjacent pages, and the exclusions page is the wordier of the two —
        # so "what's the coverages" was answered with the suicide clause.
        if broad_coverage and page.id.endswith("/exclusions"):
            page_relevance -= 0.8
        elif intent is Intent.exclusion and page.id.endswith("/exclusions"):
            page_relevance += 0.8

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
            # And the same at section level. A product page carries a "What is
            # not covered" section that is a bare pointer to the exclusions
            # page; it outscored the description when the customer asked what
            # the plan covers, so the answer opened by telling them where the
            # exclusions were.
            # A section whose whole body is a cross-reference — "The complete
            # list is on the exclusions page" — asserts nothing and yields no
            # claim, so composing from it spends a slot on a signpost. The
            # linter exempts these from the source-ref rule for the same
            # reason: the substance lives on the page they point at.
            if _is_pointer_only(body):
                section_relevance -= 1.0
            # A procedural question is answered by the section that carries the
            # procedure, and by no other. "How to buy?" was answered with 467
            # words of travel cover because nothing steered selection toward
            # the "How to buy" section — 101 products have one. Where the
            # matching section exists it wins outright; where it does not, the
            # cover sections are demoted so the turn reaches the honest
            # shortfall instead of reciting benefits at someone asking to buy.
            # Boost, never demote. Suppressing the other sections starved the
            # answer of claims: a "How to buy" section is a channel table with
            # no `[src:]` markers, so on its own it produces no claim at all
            # and reference-integrity refuses the turn. The procedural section
            # leads; the rest still carry the citations.
            procedural = PROCEDURAL_HEADINGS.get(intent)
            leads = procedural is not None and bool(procedural.search(heading))
            # The overview sections, for a request that wants the overview.
            # "What this plan is" is 101 characters and scores near nothing on
            # word overlap, so without this the deepest subsection wins a
            # question that asked for the shape of the product.
            # `broad_coverage` only — not every turn without a question word.
            # "travel baggage per-item sub-limit" has none either, and it names
            # a specific benefit; boosting the overview there took the Baggage
            # section's figure out of the answer.
            #
            # A lead, not a score: scoring it inflates the relative floor below
            # and cuts the cover page the overview is supposed to introduce.
            leads = leads or (broad_coverage and bool(OVERVIEW_HEADING_RE.search(heading)))
            heading_is_exclusion = bool(re.search(r"not covered|exclusion|exclude", heading, re.I))
            if heading_is_exclusion and broad_coverage:
                section_relevance -= 0.8
            elif heading_is_exclusion and intent is Intent.exclusion:
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
            if score > 0 or leads:
                scored.append(
                    Selection(
                        page=page, heading=heading, body=_capped(body), score=round(score, 3), lead=leads
                    )
                )

    # The procedural section leads whatever it scored — a "How to buy" section
    # is a channel table and scores poorly on word overlap with "how do I buy".
    scored.sort(key=lambda s: (not s.lead, -s.score, s.page.id, s.heading))
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
    # Relative to the best *scored* section, not to the lead: the lead is kept
    # regardless, and the others are kept on their own merits, so the answer
    # opens with the procedure and still carries citable claims.
    floor = max(0.2, max(s.score for s in scored) * 0.72)
    return [s for s in scored if s.lead or s.score >= floor][:limit]


def _is_pointer_only(body: str) -> bool:
    """Is this section nothing but a link to another page?"""
    stripped = SOURCE_REF_RE.sub("", body).strip()
    if not stripped or "](" not in stripped:
        return False
    residual = re.sub(r"\[([^\]]*)\]\([^)]*\)", " ", stripped)
    return len(residual.split()) <= 12


TIER_PLACEHOLDER = "depends on your plan tier"
#: The compiler's own pointer at the foot of a headline-benefits section
#: (`wiki.py`). It is a cross-reference, not a statement about the product, so
#: it does not make a paragraph of tier stand-ins substantive.
CROSS_REFERENCE_RE = re.compile(r"^full benefit detail is on the benefits page\.?$", re.I)


def placeholder_only(paragraph: str) -> bool:
    """Every clause here is a stand-in for a figure this session cannot see.

    `drop_unresolved` keeps such a sentence so the page stays cited, and that
    is right. But a paragraph made of nothing else says only "there is a
    number and I cannot show it to you", and a summary that opens with two of
    them tells the customer nothing about the product. Order it last; it is
    still an answer, just not the first thing to say.
    """
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", paragraph) if s.strip()]
    return bool(sentences) and all(TIER_PLACEHOLDER in s or CROSS_REFERENCE_RE.match(s) for s in sentences)


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


#: A heading that *governs* the text beneath it rather than labelling it.
#:
#: The distinction is not cosmetic. Asked what travel insurance covers, the bot
#: answered "Travel Insurance covers you upon the death of the Insured
#: Person(s)" — a faithful rendering of a list it was handed as established
#: fact, whose heading, `This Insurance shall be cancelled`, had been dropped.
#: The list said when cover *ends*. Without the heading there is nothing in the
#: words to say so, and no model could recover it.
#:
#: This corpus is full of them: "What is not covered" heads 101 sections, "The
#: following are excluded" 47, "We will not pay for" 24, "Your policy will end
#: when one of these events happens first" 38. Matching contract polarity
#: language is narrow and stable in a way that matching customer vocabulary is
#: not — these phrases are drafting convention, not the words people improvise.
GOVERNING_HEADING_RE = re.compile(
    r"\b(?:we (?:will|shall) not|will not (?:pay|be)|not covered|no benefit|not be payable"
    r"|are excluded|is excluded|following are|shall be cancelled|be cancelled|will end"
    r"|shall cease|ceases?|does not cover|do not cover|excluded from|if you are|prohibited)\b",
    re.I,
)


def clean_heading(heading: str) -> str:
    """A heading fit to lead a claim.

    The compiler now strips source markers and entities at the point it writes
    a heading, but a served bundle may predate that, and a heading carrying
    `[src:...]` becomes a claim carrying it — which the entailment judge will
    not vouch for, correctly.
    """
    heading = SOURCE_REF_RE.sub("", heading or "")
    for entity, char in (("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"'), ("&#39;", "'")):
        heading = heading.replace(entity, char)
    return " ".join(heading.split()).strip(" .,;:")


def under_heading(heading: str, text: str) -> str:
    """A fact stated together with the heading that governs it.

    Applied to every claim, not only the governing ones: the claims are what
    the model is told have been established, and a heading is free context
    there. It is the customer-facing prose that has to be selective.
    """
    heading = clean_heading(heading)
    if not heading or heading.lower() in text[: len(heading) + 8].lower():
        return text
    return f"{heading}: {text}"


def lead_with_heading(heading: str, prose: str) -> str:
    """Prose led by its heading where the heading carries the polarity.

    A label — "Premium", "General Definitions" — adds nothing a reader needs
    and makes the answer read like a table of contents, so it stays off.
    """
    heading = clean_heading(heading)
    if not heading or not GOVERNING_HEADING_RE.search(heading):
        return prose
    if heading.lower() in prose[: len(heading) + 8].lower():
        return prose
    return f"{heading.rstrip(':.')}: {prose}"


#: A sentence whose figure did not resolve. `resolve_transclusions` writes
#: `[unavailable]` rather than inventing the number, which is right — but the
#: sentence around it says nothing and reads as a broken template. Anonymous
#: sessions carry `tier = "UNKNOWN"`, so every tier-varying figure lands here
#: and customers were shown "The child limit for the plan tier held is
#: [unavailable]". The turn already appends the "sign in for tier-specific
#: limits" caveat, which is the honest half of this; the holed sentence is not.
#: The two shapes a tier-varying figure takes in the corpus. Predicate first —
#: "The child limit for the plan tier held is [unavailable]" — then the figure
#: used as a noun mid-sentence: "Reimbursed up to [unavailable] for the plan
#: tier held, and emergency dental treatment ... is included".
_TIER_PREDICATE_RE = re.compile(r"\s*(?:for the plan tier held)?\s*(?:is|are)\s*\[unavailable\]", re.I)
_TIER_NOUN_RE = re.compile(r"\[unavailable\](?:\s*for the plan tier held)?", re.I)
#: Anything still holding a placeholder after both — no template to work with,
#: so the sentence goes rather than shipping the hole.
_UNRESOLVED_SENTENCE_RE = re.compile(r"[^.!?\n]*\[unavailable\][^.!?\n]*[.!?]?", re.I)


def drop_unresolved(text: str) -> str:
    """Say what the sentence was for, rather than showing the hole in it.

    Deleting the sentence was the first attempt and it lost the citation: on a
    tier-varying benefit that sentence is the *only* one the benefits page
    contributes, so removing it removed the page from the answer's sources. The
    page is still where "this benefit exists, and its limit depends on your
    tier" comes from, and a customer is better served knowing that than shown
    either a placeholder or nothing.

    Rewritten clause by clause, not sentence by sentence. One sentence can
    carry two of these — "the child limit ... is [unavailable] and the home
    content cover limit ... is [unavailable]" — and replacing the sentence
    wholesale dropped the second benefit. Worse, the figure is often just a
    noun in the middle of a sentence that goes on to say something real:
    deleting "Reimbursed up to [unavailable] for the plan tier held, and
    emergency dental treatment following an accident is included" threw away
    the dental cover along with the missing number, and with it the only claim
    the benefits page contributed — so the page stopped being cited at all.
    """
    if "[unavailable]" not in text:
        return text
    text = _TIER_PREDICATE_RE.sub(" depends on your plan tier", text)
    text = _TIER_NOUN_RE.sub("an amount that depends on your plan tier", text)
    return _UNRESOLVED_SENTENCE_RE.sub("", text) if "[unavailable]" in text else text


def clean_prose(text: str) -> str:
    """Strip machine markup for display; the bindings live on the contract."""
    # Source refs come out first. They contain dots — `www.tiq.com.sg/2026-08-25`
    # — and `drop_unresolved` splits on sentence punctuation, so running it
    # first cut a ref in half and left the tail in the prose, where its digits
    # read as unbound numbers and the numeric-binding gate refused the answer.
    text = SOURCE_REF_RE.sub("", text)
    text = drop_unresolved(text)
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
                # A product need not carry its own number, and where the crawl
                # offered only a claims-only or emergency-assistance line the
                # compiler now declines to bind one at all. The channel's
                # general contact is the right answer then — better than the
                # travel emergency-assistance hotline, which is what a customer
                # asking to *buy* travel insurance used to be given.
                hotline=binding.hotline or (spec.hotline if spec else None),
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
    ask: Ask | None = None,
) -> Composition:
    ask = ask or ask_about(question, product, benefits)
    selections = select_sections(pages, question, idf=idf, benefits=benefits, product=product, ask=ask)
    if no_confident_match:
        # Nothing cleared the confidence floor and the raw corpus had nothing
        # either. Composing from the least-bad pages is how an assistant
        # invents a product it does not sell (§F.1) — say so and hand off.
        return Composition(
            answer=GroundedAnswer(
                answer=shortfall(question, product),
                handoff=True,
                advice_flag=advice_required,
                confidence=0.0,
                unresolved=["no page matched the question above the confidence floor"],
            )
        )
    if not selections:
        answer = GroundedAnswer(
            answer=shortfall(question, product),
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
    contested_notes: list[str] = []
    contested_shown: set[str] = set()
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
            contested = (product_key or "", figure.benefit_code, figure.attribute) in bundle.contested
            figures_detail.append(
                {
                    "label": f"{figure.benefit_code}.{figure.attribute}",
                    "value": figure.text,
                    "row_id": figure.row_id,
                    "source_ref": figure.source_ref,
                    "page": selection.page.id,
                    "contested": "true" if contested else "",
                }
            )
            if contested and figure.text not in contested_shown:
                # The compiler filed a ticket: two published sources disagree
                # on this figure, and the wiki carries the higher-authority
                # value. Nothing at answer time knew that. A customer can read
                # either surface, so the answer says which this is rather than
                # presenting a disputed number as settled.
                contested_shown.add(figure.text)
                contested_notes.append(
                    f"Our published pages differ on the {figure.benefit_code.replace('_', ' ')} figure; "
                    f"{figure.text} is the value from the policy wording, which takes precedence."
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
            text = clean_prose(paragraph)
            # A paragraph that was nothing but an unresolved figure cleans down
            # to nothing. An empty claim asserts nothing and cites a page for it.
            if not text:
                continue
            claims.append(
                Claim(
                    text=under_heading(selection.heading, text),
                    source_id=selection.page.id,
                    locator=locator,
                )
            )

        prose = clean_prose(resolved.text)
        if prose:
            paragraphs.append(lead_with_heading(selection.heading, prose))

    # Drop the tier stand-ins when anything substantive survives them: the
    # trailing "limits vary by plan tier" line already tells the customer
    # exactly this, and ordering them last was not enough — the model rewrites
    # the facts freely and put them back at the front of the summary. When
    # they are all there is, they stay: then they are the answer, and the
    # page they cite is the only page the turn has.
    # Only for a summary, and only when something substantive survives. Asked
    # for the shape of a product, "the child limit depends on your plan tier"
    # says nothing the trailing tier line does not already say, and the model
    # put it first however the facts were ordered. Asked about a specific
    # benefit, that same sentence is the answer — dropping it there cost the
    # trip-cancellation page its only claim and the turn its delivery.
    if ask.scope == "overview":
        substantive = [p for p in paragraphs if not placeholder_only(p)]
        if substantive:
            paragraphs = substantive

    render = render_channel(bundle, product, session)
    if contested_notes:
        paragraphs.append(" ".join(contested_notes))
    # True, cited, and misleading by omission: an answer about travel cover
    # that never mentions the baggage the customer asked about is grounded
    # and passes every gate. Where the question names a benefit and no
    # selected section mentions it, say so — a statement about what was *not*
    # found, which the composer already knows and the customer cannot.
    named = asked_benefits(bundle, question) if product is not None else set()
    if named and paragraphs:
        from okf import load_vocabulary

        # Judged against every page that was *loaded*, not only the sections
        # selected: "is my luggage covered" was composed from the exclusions
        # page while the benefits page — loaded, and headed "Baggage" — went
        # unselected, and the line then claimed the pages did not address
        # baggage. That is a selection defect, not an absence, and the scope
        # line must not turn the one into the other.
        shown = " ".join(f"{p.frontmatter.title} {p.body}" for p in pages).lower()
        vocabulary = load_vocabulary(bundle.root)

        def addressed(code: str) -> bool:
            # A benefit is addressed if the pages use any of its customer
            # words ("luggage", "suitcase") or its own name's words
            # ("baggage"); "baggage_loss" is never spelled out on a page.
            terms = [t.lower() for t in vocabulary.get(code, [])]
            words = [w for w in code.replace("_", " ").split() if len(w) >= 4]
            return any(t in shown for t in terms) or (bool(words) and all(w in shown for w in words))

        unaddressed = sorted(code for code in named if not addressed(code))
        if unaddressed:
            what = ", ".join(code.replace("_", " ") for code in unaddressed)
            paragraphs.append(f"The pages I answer from do not address {what} for {product_name(product)}.")
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
