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
    unknown = "unknown"


#: Ordered: the first match wins, so the specific patterns precede the broad
#: ones. `coverage` deliberately sits near the end — "what does X cover" is the
#: catch-all a dozen sharper questions would otherwise be swallowed by. And
#: `definition` precedes `limit` because "what does excess mean" is a request
#: for a definition that happens to name a limit word, not a request for a
#: number.
_PATTERNS: tuple[tuple[Intent, re.Pattern[str]], ...] = (
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
    (
        Intent.claim,
        re.compile(
            # Process language only. "I am claiming for wear and tear, will you
            # pay?" is asking whether something is covered, not how to lodge a
            # claim — reading it as process sent exclusion questions to a gate
            # that wanted a claims page cited.
            r"\b(how (do|can) i claim|make a claim|file a claim|submit a claim"
            r"|claim (process|procedure|form|status)"
            r"|what (do|documents) .{0,24}(need|submit|send).{0,24}claim)\b",
            re.I,
        ),
    ),
    (
        Intent.application,
        re.compile(
            r"\b(how (do|can) i (buy|purchase|apply|take out|sign up)|steps to (buy|take out|apply)"
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
    (Intent.coverage, re.compile(r"\b(cover(s|ed|age)?|include(s|d)?|protect(s|ion)?|benefit(s)?)\b", re.I)),
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
    for intent, pattern in _PATTERNS:
        if pattern.search(text):
            return intent
    return Intent.unknown


@dataclass(frozen=True)
class Requirement:
    """What an answer must show before it counts as having answered."""

    #: A figure bound to a benefit-table row or an SOR field.
    needs_figure: bool = False
    #: A cited page whose id ends in one of these.
    needs_page_suffix: tuple[str, ...] = ()
    #: A cited page of one of these types.
    needs_page_type: tuple[str, ...] = ()
    #: Words the answer must contain to be about the right subject at all.
    needs_any_term: tuple[str, ...] = ()
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


#: What each intent demands. Intents absent from this table are unconstrained —
#: `coverage`, `definition` and `unknown` are answerable from ordinary prose and
#: demanding structure of them would refuse customers for asking broadly.
REQUIREMENTS: dict[Intent, Requirement] = {
    Intent.limit: Requirement(needs_figure=True, satisfied_by_unresolved=True),
    Intent.exclusion: Requirement(
        needs_page_suffix=("/exclusions",),
        needs_any_term=("exclud", "not covered", "exclusion"),
    ),
    Intent.claim: Requirement(needs_page_type=("journey",), needs_any_term=("claim",)),
    Intent.application: Requirement(
        needs_page_type=("journey", "channel"),
        needs_any_term=("buy", "purchase", "apply", "quote", "online"),
    ),
    Intent.eligibility: Requirement(
        needs_any_term=("eligib", "age", "resident", "citizen", "pass holder", "qualify", "who can")
    ),
    # Nothing in this corpus carries a premium, a renewal date or a downloadable
    # document. These are not gaps to paper over with the nearest page — they
    # are the questions where improvising is most convincing and most costly.
    Intent.price: Requirement(needs_any_term=("premium", "cost", "price")),
    Intent.renewal: Requirement(needs_any_term=("renew", "expire", "cancel", "free-look", "cooling")),
    Intent.document: Requirement(needs_any_term=("wording", "policy document", "download", "contract")),
}
