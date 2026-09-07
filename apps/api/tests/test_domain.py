"""v2.8 — what the system will not answer, and what it says instead.

Four kinds of turn that were being answered from the corpus and should not
have been. Each was found by asking the way a customer asks, and each was
delivered with the full apparatus of a real answer behind it — cited pages,
bound figures, passing gates — which is what made them worth fixing.

    what is the capital of france   → a business-interruption clause
    should i buy bitcoin or tesla   → four paragraphs of reinstatement
                                       conditions, adviser line appended
    mediacl insurance               → "I could not establish that ... let me
                                       pass you to a colleague"
    the policy number for John Tan  → how to log in and open My Policies
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from api.clarify import did_you_mean, typo_clarification
from api.domain import DOMAIN_RE, decline, in_domain, off_domain
from api.guardrails import named_third_party
from api.guidance import adviser_referral
from api.pipeline import answer_question
from api.settings import Settings
from harness import GroundedAnswer
from harness.gates import ADVICE_SEEKING_RE, GateContext, gate_domain

from conftest import make_session
from okf import Bundle

REAL = Path(__file__).resolve().parents[3] / "okf-real"
real_only = pytest.mark.skipif(
    not (REAL / "catalogue.yaml").exists(), reason="real bundle not in this checkout"
)


def ask(bundle: Bundle, settings: Settings, question: str, **kw: Any):  # type: ignore[no-untyped-def]
    return answer_question(bundle, question, make_session(**kw), settings)


# --- the domain test -----------------------------------------------------------


OFF_DOMAIN = [
    "what is the capital of france",
    "give me a recipe for chicken rice",
    "who won the election",
    "how many CPF ordinary account dollars can i use to top up my special account",
    "cool, anyway what do you think about the weather lately",
]

#: Turns something in the words marks as ours: a product, a shape `classify`
#: reads, an incident, or a word from the lexicon.
SIGNALLED = [
    "what does travel insurance cover",
    "do I need pre-authorisation?",
    "Can I appeal an underwriting decision?",
    # An incident. The place and the body part are words the corpus has never
    # seen, and it is the incident that makes the turn ours, not the vocabulary.
    "the airline lost my suitcase in Tokyo",
    "I fell off my bike and broke my wrist",
    "someone broke into my flat last night",
    "I am in hospital in Bangkok and I do not know what to do",
]

#: Turns nothing marks as ours, kept by the other half of the test: every word
#: in them is one the documentation itself uses. Ordinary follow-ups, and the
#: reason the lexicon alone is not the rule.
FAMILIAR_WORDS = [
    "what do I have to declare?",
    "how much will it be?",
]


@pytest.mark.parametrize("question", OFF_DOMAIN)
def test_a_question_about_something_else_is_off_domain(bundle: Bundle, question: str) -> None:
    assert off_domain(bundle, question)


@pytest.mark.parametrize("question", SIGNALLED)
def test_a_question_that_says_it_is_about_insurance_is_kept(bundle: Bundle, question: str) -> None:
    assert in_domain(bundle, question)
    assert not off_domain(bundle, question)


@real_only
@pytest.mark.parametrize("question", FAMILIAR_WORDS)
def test_a_follow_up_in_the_corpus_own_words_is_kept(question: str) -> None:
    """Nothing signals the domain, and every word is one the documentation
    uses — so the second half of the test keeps it. This is the pairing: on the
    lexicon alone these two are refused.

    Against the real corpus, because the claim is about *that* corpus's
    vocabulary. The seed bundle is a few fixture pages and has never seen
    "declare", which is the behaviour working, not failing: a bundle that does
    not use a word cannot answer questions about it either.
    """
    bundle = Bundle.load(REAL)
    assert not in_domain(bundle, question)
    assert not off_domain(bundle, question)


def test_both_halves_of_the_test_are_load_bearing(bundle: Bundle) -> None:
    """Neither condition alone is the rule.

    "The airline lost my suitcase in Tokyo" names a city the corpus has never
    seen and is squarely a travel claim; "what do I have to declare?" carries
    no listed domain word and is an ordinary underwriting follow-up. Dropping
    either half refuses one of them.
    """
    incident = "the airline lost my suitcase in Tokyo"
    assert in_domain(bundle, incident)
    assert not DOMAIN_RE.search("what do I have to declare?")


def test_a_greeting_is_not_off_topic(bundle: Bundle) -> None:
    assert not off_domain(bundle, "hi there")


# --- what it replies -----------------------------------------------------------


def test_the_decline_offers_a_person_and_claims_nothing(bundle: Bundle) -> None:
    answer = decline(bundle)
    assert answer.off_domain and answer.handoff
    assert not answer.claims
    assert answer.destinations, "a customer sent away is sent somewhere"


def test_the_gate_blocks_an_off_domain_reply(bundle: Bundle) -> None:
    ctx = GateContext(
        answer=decline(bundle),
        bundle=bundle,
        session=make_session(),
        question="x",
        loaded_page_ids=[],
    )
    assert gate_domain(ctx).blocking
    ordinary = GateContext(
        answer=GroundedAnswer(answer="x"),
        bundle=bundle,
        session=make_session(),
        question="x",
        loaded_page_ids=[],
    )
    assert not gate_domain(ordinary).blocking


@real_only
@pytest.mark.parametrize("question", OFF_DOMAIN)
def test_an_off_domain_turn_is_not_delivered(question: str) -> None:
    """The behaviour the field test asserts: nothing about a product is said,
    and the envelope does not claim to have answered."""
    settings = Settings(bundle_path=REAL)
    envelope, _ = ask(Bundle.load(REAL), settings, question)
    assert not envelope.delivered
    assert envelope.answer.handoff
    assert not envelope.answer.claims


@real_only
def test_an_off_topic_turn_mid_conversation_is_not_answered_from_the_product() -> None:
    """A conversation that has been about travel does not make the next thing
    said a question about travel."""
    bundle, settings = Bundle.load(REAL), Settings(bundle_path=REAL)
    envelope, _ = answer_question(
        bundle,
        "cool, anyway what do you think about the weather lately",
        make_session(),
        settings,
        history=["what does travel insurance cover"],
    )
    assert not envelope.delivered
    assert "travel" not in envelope.answer.answer.lower()


# --- a recommendation is a person's job ----------------------------------------


@pytest.mark.parametrize(
    "question",
    [
        "should i buy bitcoin or tesla stock",
        "should i cancel my policy with great eastern and move to etiqa",
        "is NTUC Income better than Etiqa for term life",
        "Which plan is best for me?",
    ],
)
def test_a_recommendation_is_recognised_as_one(question: str) -> None:
    assert ADVICE_SEEKING_RE.search(question)


def test_the_referral_flags_advice_and_recommends_nothing(bundle: Bundle) -> None:
    answer = adviser_referral(bundle)
    assert answer.advice_flag and answer.handoff
    assert not answer.claims
    lowered = answer.answer.lower()
    assert "i recommend" not in lowered and "you should buy" not in lowered


@real_only
def test_a_recommendation_is_not_answered_from_a_policy_page() -> None:
    """Before this, "should i buy bitcoin or tesla stock" returned the
    business-property reinstatement conditions with the adviser sentence
    appended to them."""
    settings = Settings(bundle_path=REAL)
    envelope, _ = ask(Bundle.load(REAL), settings, "should i buy bitcoin or tesla stock")
    assert envelope.answer.advice_flag
    assert not envelope.answer.claims
    assert "reinstating" not in envelope.answer.answer.lower()


# --- a misspelt product is asked about, not refused ------------------------------


@real_only
@pytest.mark.parametrize(
    ("typo", "expected"),
    [("mediacl", "accident-health"), ("cancr", "cancer-insurance"), ("trvael", "travel")],
)
def test_a_near_miss_resolves_to_the_product_meant(typo: str, expected: str) -> None:
    hits = did_you_mean(Bundle.load(REAL), typo)
    assert any(expected in page_id for page_id in hits), hits


@real_only
@pytest.mark.parametrize("token", ["crop", "kidnap", "aviation"])
def test_a_line_we_do_not_carry_is_not_guessed_at(token: str) -> None:
    """The near-miss resolver must not turn "crop insurance" into whichever
    product sorts nearest. A line we do not write stays a refusal."""
    assert did_you_mean(Bundle.load(REAL), token) == []


@real_only
def test_one_near_miss_is_still_worth_asking_about() -> None:
    """`clarification` needs two options because a tie of one is not a choice.
    A misspelling resolving to one product is a different thing."""
    bundle = Bundle.load(REAL)
    asked = typo_clarification(bundle, did_you_mean(bundle, "cancr"))
    assert asked is not None and asked.clarifying
    assert "cancer" in asked.answer.lower()


@real_only
@pytest.mark.parametrize("question", ["mediacl insurance", "cancr plan"])
def test_a_typo_asks_instead_of_handing_off(question: str) -> None:
    settings = Settings(bundle_path=REAL)
    envelope, _ = ask(Bundle.load(REAL), settings, question)
    assert envelope.answer.clarifying
    assert not envelope.answer.handoff


# --- somebody else's record ------------------------------------------------------


@real_only
def test_a_named_third_partys_record_is_refused() -> None:
    bundle, settings = Bundle.load(REAL), Settings(bundle_path=REAL)
    assert named_third_party(bundle, "what is the policy number for John Tan")
    envelope, _ = ask(bundle, settings, "what is the policy number for John Tan")
    assert not envelope.delivered


@real_only
@pytest.mark.parametrize(
    "question",
    [
        # Capitalised exactly like a person, and a product. Only the catalogue
        # separates them, which is why this check needs the bundle.
        "Where can I find the policy wording for Tiq Home Insurance?",
        "policy details for Invest Smart Vista",
        "what is my policy number",
    ],
)
def test_a_product_is_not_mistaken_for_a_person(question: str) -> None:
    assert not named_third_party(Bundle.load(REAL), question)
