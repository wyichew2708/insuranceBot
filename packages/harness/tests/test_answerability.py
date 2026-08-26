"""The eighth gate: does the answer address what was asked?

The other seven are provenance checks. Every one of them passes an answer about
travel-delay thresholds given to someone who asked what the policy costs a year,
because the thresholds really did come from a page we really did load. Measured
on the real corpus that was 1,177 of 3,130 failures — the largest single class.

These tests pull in two directions on purpose. The gate has to refuse the
genuinely unanswered question, and it has to leave everything else alone: a
guardrail that refuses real customers is worse than the failure it prevents,
and the first three drafts of the intent classifier each refused something
legitimate.
"""

from __future__ import annotations

import pytest
from harness.contracts import Claim, Figure, GroundedAnswer
from harness.gates import GateContext, gate_answerability
from harness.intent import REQUIREMENTS, Intent, classify

from okf import Bundle


def _ctx(
    bundle: Bundle, question: str, answer: GroundedAnswer, loaded: list[str] | None = None
) -> GateContext:
    return GateContext(
        answer=answer,
        bundle=bundle,
        session=__import__("harness").Session(session_id="t"),
        question=question,
        loaded_page_ids=loaded or [],
    )


# --- the taxonomy ----------------------------------------------------------


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("How much does Travel Insurance cost me a year?", Intent.price),
        ("What is the premium for home insurance?", Intent.price),
        ("When does my policy renew?", Intent.renewal),
        ("Where do I download the policy document?", Intent.document),
        ("How do I make a claim on Travel Insurance?", Intent.claim),
        ("Can I apply if I am more than 70 years old?", Intent.eligibility),
        ("What are the steps to take out Travel Insurance?", Intent.application),
        ("Can I get Home Insurance from a broker?", Intent.application),
        ("What is the baggage limit on Travel Insurance?", Intent.limit),
        ("Is there a cap on contents cover?", Intent.limit),
        ("What does excess mean?", Intent.definition),
        ("What does Home Insurance cover?", Intent.coverage),
    ],
)
def test_intents_are_read_from_the_question(question: str, expected: Intent) -> None:
    assert classify(question) is expected


@pytest.mark.parametrize(
    "question",
    [
        # Each of these was misread by an earlier draft and refused for it.
        "Tell me about excess amount.",
        "What is excess amount and what does it give me?",
        "I am claiming for wear and tear. Will Home Insurance pay?",
        "travel baggage limit",
        "",
    ],
)
def test_ambiguous_questions_fall_through_rather_than_being_forced(question: str) -> None:
    """`unknown` is a working answer. The gate skips it, so an ambiguous
    question is answered broadly instead of refused for being broad."""
    assert REQUIREMENTS.get(classify(question)) is None


# --- the gate --------------------------------------------------------------


def test_a_limit_question_answered_without_a_figure_is_refused(bundle: Bundle) -> None:
    answer = GroundedAnswer(
        answer="Travel Insurance covers overseas medical expenses and baggage.",
        claims=[Claim(text="covers baggage", source_id="product/general/travel")],
    )
    result = gate_answerability(_ctx(bundle, "What is the baggage limit on Travel Insurance?", answer))
    assert result.blocking
    assert "limit" in result.detail


def test_the_same_question_passes_once_a_figure_is_bound(bundle: Bundle) -> None:
    answer = GroundedAnswer(
        answer="Baggage is covered up to S$3,000.",
        figures=[
            Figure(
                label="baggage_loss.limit",
                text="S$3,000",
                table_row_id="travel:2026.1:tier-1:baggage_loss.limit",
            )
        ],
    )
    result = gate_answerability(_ctx(bundle, "What is the baggage limit on Travel Insurance?", answer))
    assert not result.blocking


def test_a_price_question_is_refused_because_no_price_exists_anywhere(bundle: Bundle) -> None:
    """The most-asked question about any policy, and the one with no answer in
    this corpus. An invented premium is the most convincing and most costly
    thing the system could say."""
    answer = GroundedAnswer(
        answer="Travel delay pays once departure is delayed beyond 6 hours.",
        claims=[Claim(text="delay threshold", source_id="product/general/travel")],
    )
    result = gate_answerability(_ctx(bundle, "How much does Travel Insurance cost a year?", answer))
    assert result.blocking


def test_a_handoff_is_never_refused_again(bundle: Bundle) -> None:
    """Refusing is already the safe outcome; blocking it a second time would
    only replace one refusal with another."""
    result = gate_answerability(
        _ctx(bundle, "How much does it cost?", GroundedAnswer(answer="x", handoff=True))
    )
    assert not result.blocking


def test_a_broad_coverage_question_is_left_alone(bundle: Bundle) -> None:
    answer = GroundedAnswer(
        answer="Travel Insurance covers overseas medical expenses, cancellation and baggage.",
        claims=[Claim(text="covers", source_id="product/general/travel")],
    )
    assert not gate_answerability(_ctx(bundle, "What does Travel Insurance cover?", answer)).blocking


def test_one_satisfied_clause_is_enough(bundle: Bundle) -> None:
    """A requirement is a disjunction. An eligibility answer that talks about
    age satisfies it without citing any particular page."""
    answer = GroundedAnswer(answer="Cover is available up to age 70 for Singapore residents.")
    assert not gate_answerability(_ctx(bundle, "Can I apply if I am 70?", answer)).blocking


def test_a_claim_question_passes_when_a_journey_page_is_cited(bundle: Bundle) -> None:
    answer = GroundedAnswer(
        answer="Submit the form and we acknowledge with a reference.",
        claims=[Claim(text="submit", source_id="journey/claim/travel")],
    )
    assert not gate_answerability(_ctx(bundle, "How do I make a claim on Travel Insurance?", answer)).blocking


def test_the_detail_says_what_was_asked_and_what_was_missing(bundle: Bundle) -> None:
    """An operator reading a refusal needs to know which intent went unmet —
    otherwise the only way to tune the gate is by feel."""
    answer = GroundedAnswer(answer="Travel Insurance covers baggage.")
    result = gate_answerability(_ctx(bundle, "What is the baggage limit on Travel Insurance?", answer))
    assert "limit" in result.detail and "none of it" in result.detail


def test_an_unresolved_figure_answers_a_limit_question(bundle: Bundle) -> None:
    """An anonymous customer asking the medical expenses limit cannot be given
    one — it varies by plan tier. Saying so and inviting them to sign in tells
    them something a flat refusal does not, and it is what the curated golden
    suite has always required. "I don't know" and "I know why I can't tell you
    yet" are different answers."""
    answer = GroundedAnswer(
        answer="Limits vary by plan tier, so sign in and I'll give you the exact figure.",
        unresolved=["product/general/travel/benefits:medical_expenses.limit"],
    )
    assert not gate_answerability(_ctx(bundle, "What is the medical expenses limit?", answer)).blocking


def test_an_unresolved_marker_does_not_excuse_a_price_question(bundle: Bundle) -> None:
    """Nothing in the corpus carries a premium, so an unresolved marker there is
    not an explanation — just an absence wearing one."""
    answer = GroundedAnswer(
        answer="The amount payable for the plan tier held is [unavailable].",
        unresolved=["product/general/travel/benefits:travel_delay.cap"],
    )
    assert gate_answerability(_ctx(bundle, "How much does Travel Insurance cost a year?", answer)).blocking


def test_a_page_that_declares_the_intent_settles_it(bundle: Bundle) -> None:
    """A published eligibility answer reads "Singaporean, PR, Work Pass holder"
    and contains none of "eligible", "age" or "qualify". Refusing it for that
    would be refusing the insurer's own answer, so a page that declares which
    intents it answers is taken at its word."""
    from okf.page import Frontmatter, Page, PageType, Status

    # `faq_intents` is an extra field the compiler writes; Frontmatter allows
    # extras, so it round-trips through validation rather than the constructor.
    fm = Frontmatter.model_validate(
        {
            "id": "product/general/travel/faq",
            "title": "Travel — Published FAQs",
            "type": PageType.product,
            "status": Status.approved,
            "jurisdiction": "SG",
            "faq_intents": ["eligibility"],
        }
    )
    bundle.pages["product/general/travel/faq"] = Page(frontmatter=fm, body="## Who can buy?\n\nAnyone.\n")
    answer = GroundedAnswer(
        answer="Singaporean, Permanent Resident, or a Work Pass holder.",
        claims=[Claim(text="who can buy", source_id="product/general/travel/faq")],
    )
    try:
        assert not gate_answerability(_ctx(bundle, "Who can buy travel insurance?", answer)).blocking
    finally:
        bundle.pages.pop("product/general/travel/faq", None)
