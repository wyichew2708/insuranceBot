"""What a question is asking for, and what would count as having answered it.

The seven verification gates are all provenance checks. They prove a figure
came from a named table row, that a cited page was read, that contact details
belong to the session's channel. Not one of them compares the answer to the
*question* — so an answer about travel-delay thresholds passes every gate when
the customer asked what the policy costs a year, because the thresholds really
did come from a page we really did load.

Measured on the real corpus, that is the single largest failure class: of 3,130
failing cases, 1,177 were answered when nothing in the corpus could answer
them. Not hallucinated — every figure still bound to a row — just fluent,
grounded, and about something else.

This module is the missing half. An intent says what kind of thing the customer
asked for; `Requirement` says what evidence would settle it. The gate that uses
them refuses when the evidence is absent rather than reaching for the nearest
page, and the same taxonomy tells the compiler which source is authoritative
for which question — a policy wording owns limits and exclusions and is largely
silent on eligibility, which is most of what customers actually ask.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class Intent(str, Enum):
    """What the customer wants, not what they typed."""

    limit = "limit"  # how much does it pay
    exclusion = "exclusion"  # what is not covered
    eligibility = "eligibility"  # can I buy it, am I old enough
    application = "application"  # how do I buy it
    claim = "claim"  # how do I claim, what do you need
    price = "price"  # what does it cost
    renewal = "renewal"  # when does it renew, can I cancel
    document = "document"  # send me the wording
    coverage = "coverage"  # what does it cover, broadly
    definition = "definition"  # what does <term> mean
    smalltalk = "smalltalk"  # hello, thanks, are you a bot
    browse = "browse"  # what do you sell, show me your life plans
    entity = "entity"  # who underwrites this, which legal entity
    offer = "offer"  # is there a promotion, a discount, cashback
    unknown = "unknown"


#: Openings and closings, and the two questions every chat surface is asked
#: before anything else: what are you, and what can you do.
#:
#: Anchored end to end on purpose. "hi" is a greeting; "hi, what does travel
#: insurance cover?" is a coverage question with a greeting attached, and
#: treating the second as smalltalk would drop a real question on the floor.
#: The trailing class allows the punctuation and emphasis people actually
#: type — "hello!!", "hi there…", "thanks :)".
SMALLTALK: dict[str, re.Pattern[str]] = {
    "greeting": re.compile(
        r"^(?:hi|hey|hello|hallo|helo|hiya|yo|sup|greetings|good\s+(?:morning|afternoon|evening|day)"
        r"|hi\s+there|hey\s+there|hello\s+there)$",
        re.I,
    ),
    "thanks": re.compile(
        r"^(?:thanks?|thank\s+you(?:\s+so\s+much)?|ty|thx|cheers|much\s+appreciated"
        r"|ok(?:ay)?|got\s+it|understood|noted|great|perfect|nice)$",
        re.I,
    ),
    "farewell": re.compile(r"^(?:bye|goodbye|good\s+bye|see\s+you|see\s*ya|cya|later|that.s\s+all)$", re.I),
    "capability": re.compile(
        r"^(?:help|what\s+can\s+you\s+do|what\s+do\s+you\s+do|how\s+can\s+you\s+help"
        r"|who\s+are\s+you|what\s+are\s+you|are\s+you\s+(?:a\s+)?(?:bot|robot|human|real|ai|person)"
        r"|how\s+(?:do|does)\s+(?:this|it)\s+work|what\s+is\s+this)$",
        re.I,
    ),
}
#: Punctuation and emphasis stripped before the patterns are tried.
_TRIM_RE = re.compile(r"^[\s\W_]+|[\s\W_]+$")


def smalltalk_kind(question: str) -> str | None:
    """Which pleasantry this is, or None if the turn asks something.

    A greeting is not a question the corpus can fail to answer. Routing one
    through retrieval produces "I could not establish that from our approved
    product pages" in reply to "hi", which reads as a broken bot rather than a
    careful one — and spends a retrieval, a model call and eight gates saying
    so.
    """
    trimmed = _TRIM_RE.sub("", " ".join(question.split()))
    if not trimmed or len(trimmed.split()) > 5:
        return None
    for kind, pattern in SMALLTALK.items():
        if pattern.match(trimmed):
            return kind
    return None


#: Someone shopping rather than asking. "what life products", "looking for a CI
#: plan", "do you have pet insurance" — the customer wants to know *what
#: exists*, and every one of these was answered from a single product page's
#: prose because retrieval is built to find the best page, not to report that
#: several are relevant. Measured on the real bundle: "what life products"
#: returned the Products Liability page, having matched on the word "products".
#: The noun a shopper names: the category, not a benefit.
_OFFERING = r"(?:products?|plans?|policies|policy|insurance|cover(?:age)?s?|options?)"

BROWSE_RE = re.compile(
    # "what life products", "which plans do you have" — at most two words of
    # qualifier between the interrogative and the noun, and then either the
    # question ends or a possession verb follows. The gap is what keeps "what
    # is not covered by fire insurance?" out: four words of question sit in it,
    # and that turn is *about* a product rather than a request to see the list.
    # The qualifier may not be a verb: "what life products" is shopping, and
    # "what is the coverage" is a question about one product that happens to
    # end on the same noun.
    rf"^(?:what|which)\s+(?:(?!(?:is|are|was|were|do|does|did|can|will)\b)\w+\s+){{0,2}}{_OFFERING}\s*"
    rf"(?:(?:do|does|are|can|have)\b[\w\s]{{0,20}})?[?.!]?$"
    # Explicit shopping language, anywhere in the turn.
    r"|\b(?:looking|search(?:ing)?|shopping)\s+for\b"
    r"|\b(?:show|list)\s+me\b"
    r"|\bdo\s+you\s+(?:have|sell|offer)\b"
    r"|\bwhat\s+(?:do|can)\s+you\s+(?:sell|offer|insure)\b"
    rf"|\b(?:any\s+other|what\s+other)\s+{_OFFERING}\b",
    re.I,
)

#: Ordered: the first match wins, so the specific patterns precede the broad
#: ones. `coverage` deliberately sits near the end — "what does X cover" is the
#: catch-all a dozen sharper questions would otherwise be swallowed by. And
#: `definition` precedes `limit` because "what does excess mean" is a request
#: for a definition that happens to name a limit word, not a request for a
#: number.
_PATTERNS: tuple[tuple[Intent, re.Pattern[str]], ...] = (
    # Before price: "who is the insurer" carries no price word, but "which
    # legal entity am I claiming against" carries "claim", and the claim
    # pattern would otherwise take it. Unclassified, these fell to `unknown`,
    # which the answerability gate treats as unconstrained — so "Who is the
    # insurer behind Etiqa Autolab Package Wic?" was answered with a fire-peril
    # clause and every gate passed. 14 of 79 unsafe cases on the real corpus.
    (
        Intent.entity,
        re.compile(
            r"\b(who (?:is|are) (?:the )?(?:insurer|underwriter|company|provider)|who underwrites"
            r"|underwritten by|which (?:legal )?(?:entity|company|insurer)|legal entity"
            r"|(?:insurer|underwriter) behind|who (?:do i|am i|would i) (?:claim|claiming) (?:against|from)"
            r"|who (?:backs|issues|stands behind))\b",
            re.I,
        ),
    ),
    (
        Intent.price,
        re.compile(
            r"\b(how much (does|do|is|would) .{0,40}(cost|premium)|what.{0,12}(the )?(premium|price|cost)"
            r"|cost me|per (year|month|annum)|how much to (buy|get)|cheap(er|est)?\b)",
            re.I,
        ),
    ),
    (
        Intent.renewal,
        re.compile(r"\b(renew(s|al|ed|ing)?|auto-renew|expire(s|d)?|cancel(led|lation)?|terminate)\b", re.I),
    ),
    (
        Intent.document,
        re.compile(
            r"\b(policy (document|wording|contract)|download|send me the|copy of (the|my) (policy|wording)"
            r"|certificate of insurance|product summary)\b",
            re.I,
        ),
    ),
    # Before claim and coverage: "is there a promo on travel cover" is a
    # question about the offer, and only a promotion page can answer it.
    (
        Intent.offer,
        # "discount" only when it is not the no-claim discount, which is a
        # policy figure: "what is the maximum NCD" is a limit question, and
        # reading it as an offer refused ten private-car cases for want of a
        # promotion page.
        re.compile(
            r"\b(promo|promotion|offer|deal|voucher|cashback|rebate|sale)s?\b"
            # "discount" in a turn that mentions claims anywhere — "I have
            # not claimed in years, how far can my discount go" — is the NCD.
            r"|^(?!.*\b(?:claim(?:s|ed|ing)?|ncd|no[- ]claims?)\b).*\bdiscounts?\b",
            re.I | re.S,
        ),
    ),
    (
        Intent.claim,
        re.compile(
            # Process language only. "I am claiming for wear and tear, will you
            # pay?" is asking whether something is covered, not how to lodge a
            # claim — reading it as process sent exclusion questions to a gate
            # that wanted a claims page cited.
            r"\b(how (?:(?:do|can) i |to )claim|make a claim|file a claim|submit a claim"
            r"|claim (process|procedure|form|status)"
            r"|what (do|documents) .{0,24}(need|submit|send).{0,24}claim)\b",
            re.I,
        ),
    ),
    (
        Intent.application,
        re.compile(
            r"\b(how (?:(?:do|can) i |to )(buy|purchase|apply|take out|sign up)"
            r"|steps to (buy|take out|apply)"
            r"|walk me through buying|where (do|can) i buy"
            # "Can I get Home Insurance from a broker?" asks which route sells
            # it, not whether the customer qualifies — without this it read as
            # eligibility and was refused for not discussing age or residency.
            r"|can i (get|buy|purchase|arrange) .{0,44}\b(from|through|via)\b"
            r"|where do i go to arrange)\b",
            re.I,
        ),
    ),
    (
        Intent.eligibility,
        re.compile(
            r"\b(am i eligible|eligibilit|who can (buy|apply|purchase)|can i (buy|apply|purchase|get)"
            r"|do i qualify|age limit|more than \d+ years old|minimum age|maximum age"
            r"|can my (child|spouse|wife|husband|parent))\b",
            re.I,
        ),
    ),
    (
        Intent.exclusion,
        re.compile(
            r"\b(exclu(de|ded|sion|sions)|not covered|won'?t (you )?(pay|cover)|is .{0,30} covered)\b", re.I
        ),
    ),
    (
        Intent.definition,
        re.compile(r"\b(what (is|does) .{0,40}\bmean\b|what is meant by|define|explain what)\b", re.I),
    ),
    (
        Intent.limit,
        re.compile(
            # An interrogative frame is required, not the bare noun. "Tell me
            # about excess amount" is a customer exploring an alias, and
            # demanding a bound figure of it refused three legitimate questions
            # on the seed bundle. A limit question asks for an amount.
            # The article is required. "What is *the* excess on private car" asks
            # for a number; "what is excess amount and what does it give me" is a
            # customer meeting the word for the first time, and refusing that for
            # want of a figure is refusing a definition question.
            r"\b(what (is|are|s) (the|my|your|its) .{0,34}\b(limit|cap|excess|sub-?limit|threshold"
            r"|payout|percentage|discount)\b"
            r"|how much (does|do|is|are|will|can) .{0,34}(pay|cover|reimburse|claim|get|limit|excess)"
            r"|maximum (payout|amount|sum|percentage)|how long must|most .{0,20}will pay"
            r"|is there (a|an|any) .{0,24}(limit|cap|excess|sub-?limit))\b",
            re.I,
        ),
    ),
    # `coverages` is not a word most style guides would allow and is exactly
    # what customers type. The plural was missing, so "what's the coverages"
    # classified as unknown and the composer had no signal to prefer the cover
    # page over the exclusions page beside it.
    (
        Intent.coverage,
        re.compile(r"\b(cover(s|ed|age|ages)?|include(s|d)?|protect(s|ion)?|benefit(s)?)\b", re.I),
    ),
)


def classify(question: str) -> Intent:
    """The intent of a question, or `unknown`.

    Deliberately lexical. A model could read intent better, but this decides
    whether a customer gets an answer or a handoff, and that decision has to be
    reproducible, free, and identical on every machine — the same reasons the
    verification gates are not model calls either.
    """
    text = (question or "").strip()
    if not text:
        return Intent.unknown
    # Before the topic patterns: "help" would otherwise read as an application
    # question, and "what is this" as a definition.
    if smalltalk_kind(text):
        return Intent.smalltalk
    # Before the topic patterns, and after smalltalk. "what plans do you have"
    # would otherwise be read as coverage and answered from whichever single
    # page scored highest.
    if BROWSE_RE.search(text):
        return Intent.browse
    for intent, pattern in _PATTERNS:
        if pattern.search(text):
            return intent
    return Intent.unknown


@dataclass(frozen=True)
class Requirement:
    """What an answer must show before it counts as having answered."""

    #: A figure bound to a benefit-table row or an SOR field.
    needs_figure: bool = False
    #: ...and labelled as one of these. Without it, *any* figure satisfies the
    #: clause: "how much does travel insurance cost a year" passed on a `$350`
    #: quoted out of a policy wording about lost passports. A price question is
    #: settled by a premium, not by a number.
    needs_figure_label: tuple[str, ...] = ()
    #: A cited page whose id ends in one of these.
    needs_page_suffix: tuple[str, ...] = ()
    #: A cited page of one of these types.
    needs_page_type: tuple[str, ...] = ()
    #: Words the answer must contain to be about the right subject at all.
    needs_any_term: tuple[str, ...] = ()
    #: A rendered purchase route — the product's deep link and hotline for the
    #: session's channel. Structural rather than lexical, and that is the
    #: point: "how do I buy" is answered by a route, and no policy clause can
    #: accidentally satisfy it the way the word "purchased" once did.
    needs_channel_route: bool = False
    #: Page-id suffixes that *hold* the answer, as opposed to `needs_page_suffix`
    #: which says what a cited page must look like for the gate to be satisfied.
    #:
    #: The difference is the direction. Everything else on this class is read
    #: after an answer is written, to reject it. This is read before, to go and
    #: fetch what the question needs — which is the gap that let "how to buy"
    #: be answered from three FAQ entries that happened to repeat the word
    #: "buy", while the product's own "How to buy" section sat unread on a page
    #: that was already loaded.
    holds_answer: tuple[str, ...] = ()

    #: Whether naming what could not be established counts as answering.
    #:
    #: For a limit it does. An anonymous customer asking the medical expenses
    #: limit cannot be given one — it varies by plan tier — and the product
    #: decision is to say so and invite them to sign in, which teaches them
    #: something a flat refusal does not. That is the difference between "I
    #: don't know" and "I know why I can't tell you yet".
    #:
    #: For a price it does not. Nothing in the corpus carries a premium, so an
    #: unresolved marker there is not an explanation, just an absence.
    satisfied_by_unresolved: bool = False

    @property
    def checkable(self) -> bool:
        """Does this requirement demand anything of an answer?

        `holds_answer` steers retrieval and asks nothing of the result, so an
        entry carrying only that must not make the gate refuse. `coverage` and
        `definition` are in the table for steering alone and are deliberately
        unconstrained — refusing a customer for asking broadly is worse than
        answering them broadly. Adding them without this property failed about
        a hundred generated cases with "asked for coverage; the answer shows
        none of it".
        """
        return bool(
            self.needs_figure
            or self.needs_page_suffix
            or self.needs_page_type
            or self.needs_any_term
            or self.needs_channel_route
        )


#: What each intent demands. Intents absent from this table are unconstrained —
#: `coverage`, `definition` and `unknown` are answerable from ordinary prose and
#: demanding structure of them would refuse customers for asking broadly.
REQUIREMENTS: dict[Intent, Requirement] = {
    Intent.limit: Requirement(needs_figure=True, satisfied_by_unresolved=True, holds_answer=("/benefits",)),
    Intent.exclusion: Requirement(
        needs_page_suffix=("/exclusions",),
        needs_any_term=("exclud", "not covered", "exclusion"),
        holds_answer=("/exclusions",),
    ),
    # `needs_any_term` on a procedural intent is the same hole `price` had.
    # The clauses are OR'd, so a bare word satisfies the gate: "how to buy?"
    # was answered with 467 words of travel cover because one clause said the
    # Trip was "purchased from a registered Travel Agent", and "purchase" was
    # on the list. A procedure is not a word; it is an instruction, and these
    # are the forms an answer that actually gives one uses.
    Intent.claim: Requirement(
        needs_page_type=("journey",),
        needs_page_suffix=("/claims",),
        needs_any_term=("to make a claim", "to claim", "notify us", "submit your claim", "report the"),
        holds_answer=("/claims",),
    ),
    Intent.application: Requirement(
        needs_channel_route=True,
        needs_page_type=("journey", "channel"),
        needs_any_term=(
            "you can buy",
            "to buy",
            "buy online",
            "purchase online",
            "apply online",
            "start an application",
            "get a quote",
            "how to buy",
        ),
        # The product page's own "How to buy" section carries the channel
        # table; the empty suffix means the product page itself.
        holds_answer=("",),
    ),
    Intent.eligibility: Requirement(
        needs_any_term=("eligib", "age", "resident", "citizen", "pass holder", "qualify", "who can"),
        holds_answer=("/eligibility",),
    ),
    Intent.definition: Requirement(holds_answer=("/definitions",)),
    # `coverage` deliberately steers nothing. It is the catch-all a dozen
    # sharper questions fall into — "are wear and tear covered?" classifies
    # here and wants the *exclusions* page — so naming a page for it sends the
    # narrow cases to the wrong one. The open form ("what does it cover") is
    # steered in the composer, where the phrasing is still visible.
    # Nothing in this corpus carries a premium, a renewal date or a downloadable
    # document. These are not gaps to paper over with the nearest page — they
    # are the questions where improvising is most convincing and most costly.
    # A premium is a figure, and this corpus carries none — so requiring the
    # *word* let "We will indemnify the Insured Person(s) for cost incurred up
    # to the limit..." answer "how much does travel insurance cost a year".
    # The word appeared; the price did not. Requiring a bound figure, and
    # refusing to accept an unresolved marker in its place, makes the refusal
    # honest: there is no premium here to fetch.
    # No `needs_any_term` here, and the absence is the point. The clauses of a
    # Requirement are OR'd — any one satisfies the gate — so listing the word
    # "premium" reopened the hole the figure requirement closed: "What is the
    # premium for life insurance?" was answered with "If Your Age, gender,
    # smoker status ... is not correctly stated such that the Premium paid is
    # wrong, We reserve the rights to adjust" — a clause *about* premiums,
    # containing the word, containing no price. Every gate passed. On a
    # 1,000-case sample this was the single largest unsafe class. A price
    # question is settled by a bound premium figure or by the honest shortfall
    # ("premiums are not published in the documents I answer from"), and by
    # nothing in between.
    Intent.price: Requirement(
        needs_figure=True,
        needs_figure_label=("premium", "price", "cost"),
    ),
    # A backstop behind the deterministic entity answer in `api.entity`: if a
    # bundle declares no single underwriter and the turn reaches the composer,
    # the answer must at least cite the entity page. A product's own conditions
    # page contains the word "insurer" a hundred times and says nothing about
    # who that is.
    Intent.entity: Requirement(needs_page_type=("entity",)),
    # An offer is stated on a promotion page or nowhere. A policy clause about
    # "any other insurance covering the same damage" answered "is there a
    # promotion for home insurance" before this.
    Intent.offer: Requirement(needs_page_type=("promotion",)),
    Intent.renewal: Requirement(
        needs_any_term=("renew", "expire", "cancel", "free-look", "cooling"),
        holds_answer=("/conditions",),
    ),
    # "contract" and "premium" appear in almost every wording, so the loose
    # form passed "send me the policy wording" on an answer about renewal
    # notices. These are the words an answer that actually points at a document
    # would use.
    Intent.document: Requirement(
        needs_any_term=("policy wording", "product summary", "download", "policy document")
    ),
}
