"""v2.5 — the steps to the real answer, instead of a refusal.

The product owner's rules (2026-09-05): a number the draft cannot bind is
dropped and the rest delivered, except that who-can-buy keeps its age
requirement; a question the corpus cannot settle is answered with how to get
the answer; a price with no premium behind it is the quote steps; and two
questions in one turn are two turns, put back together.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

import pytest
from api.guidance import Topic, document_links, guidance, topic_for
from api.pipeline import _strip_unbound, answer_question
from api.settings import Settings
from api.split import split_questions
from harness import Claim, GroundedAnswer
from harness.intent import Intent

from conftest import make_session
from okf import Bundle

REAL = Path(__file__).resolve().parents[3] / "okf-real"
real_only = pytest.mark.skipif(
    not (REAL / "catalogue.yaml").exists(), reason="real bundle not in this checkout"
)


def ask(bundle: Bundle, settings: Settings, question: str, **kw: Any):  # type: ignore[no-untyped-def]
    return answer_question(bundle, question, make_session(**kw), settings)


# --- the split -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("question", "parts"),
    [
        ("what does Tiq Travel Insurance cover and how much does it cost?", 2),
        ("what is covered and what is excluded?", 2),
        ("How do I claim? And how long does it take?", 2),
        ("Does it cover flood and fire?", 1),
        ("what are the terms and conditions?", 1),
        ("I need travel insurance", 1),
        ("hello there", 1),
    ],
)
def test_a_turn_splits_only_at_a_second_question(question: str, parts: int) -> None:
    assert len(split_questions(question)) == parts


def test_the_parts_keep_their_order_and_words() -> None:
    assert split_questions("what does it cover and how much does it cost?") == [
        "what does it cover",
        "how much does it cost?",
    ]


# --- the topics ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("intent", "question", "topic"),
    [
        (Intent.price, "how much does it cost?", Topic.quote),
        (Intent.payment, "how much will I get back?", Topic.refund),
        (Intent.payment, "can I pay monthly?", Topic.payment),
        (Intent.account, "what is my policy number?", Topic.policy_record),
        (Intent.account, "can I save my application and finish it later?", Topic.application),
        (Intent.claim, "how do I make a claim?", Topic.claims),
        (Intent.renewal, "I want to cancel it", Topic.cancellation),
        (Intent.renewal, "how do I renew?", Topic.renewal),
        (Intent.document, "where is the policy wording?", Topic.documents),
        (Intent.eligibility, "who can buy this?", Topic.eligibility),
        (Intent.contact, "what are your operating hours?", Topic.contact),
        (Intent.limit, "what is the limit?", Topic.generic),
    ],
)
def test_each_intent_has_its_steps(intent: Intent, question: str, topic: Topic) -> None:
    assert topic_for(intent, question) is topic


def test_guidance_names_the_product_and_carries_no_figures(bundle: Bundle) -> None:
    product = bundle.get("product/general/travel")
    answer = guidance(bundle, bundle.root / "raw", Intent.claim, product, "how do I make a claim?")
    # A handoff with steps: the flag the contract reads, and the steps the customer follows.
    assert answer.guidance and answer.handoff
    assert [c.source_id for c in answer.claims] == ["product/general/travel"]
    assert "Claims and services" in " ".join(link.label for link in answer.destinations)
    # No bare number in the prose: the numeric-binding gate would read it as a figure.
    import re

    assert not re.search(r"\b\d[\d,]{1,}\b|\b\d+\s?%", answer.answer)


def test_an_account_topic_is_a_handoff_with_the_portal_first(bundle: Bundle) -> None:
    answer = guidance(bundle, None, Intent.account, None, "what is my policy number?")
    assert answer.handoff and answer.guidance
    assert answer.destinations[0].desk == "portal"
    assert "LoginPortal" in answer.answer


# --- the pipeline paths ---------------------------------------------------------


def test_a_price_with_no_premium_behind_it_is_the_quote_steps(bundle: Bundle, settings: Settings) -> None:
    env, _ = ask(bundle, settings, "How much does Tiq Travel Insurance cost?")
    assert env.answer.handoff and env.answer.guidance
    assert "Get a quote" in env.answer.answer
    assert not any(f.label.lower().startswith(("premium", "price")) for f in env.answer.figures)


def test_a_refund_question_after_a_cancellation_is_routed(bundle: Bundle, settings: Settings) -> None:
    env, _ = ask(
        bundle,
        settings,
        "how much will I get back?",
        policy_id=None,
    )
    # Cold, it is a limit question and answered or asked about as one.
    assert not (env.answer.handoff and "refund" in env.answer.answer.lower())
    env2, _ = answer_question(
        bundle,
        "how much will I get back?",
        make_session(policy_id=None),
        settings,
        history=["I want to cancel Tiq Travel Insurance", "I only bought it last week"],
    )
    assert env2.answer.handoff and env2.answer.guidance
    assert "refund" in env2.answer.answer.lower()


def test_two_questions_are_answered_as_two_parts_and_joined(bundle: Bundle, settings: Settings) -> None:
    env, trace = ask(bundle, settings, "what does Tiq Travel Insurance cover and how much does it cost?")
    assert trace.route["parts"] == "2"
    assert trace.route["part1"].endswith("/coverage") and trace.route["part2"].endswith("/price")
    assert env.delivered
    assert any(c.source_id.startswith("product/general/travel") for c in env.answer.claims)
    assert "Get a quote" in env.answer.answer, "the price half is the quote steps"
    assert not env.answer.handoff, "one half answered, so the turn is not a handoff"


def test_stripping_unbound_lines_keeps_the_rest_and_the_pointer() -> None:
    draft = GroundedAnswer(
        answer=(
            "You must notify us of any claim.\nNotify us within 15 days of the incident.\nKeep your receipts."
        ),
        claims=[
            Claim(text="You must notify us of any claim.", source_id="p", locator="p"),
            Claim(text="Notify us within 15 days of the incident.", source_id="p", locator="p"),
            Claim(text="Keep your receipts.", source_id="p", locator="p"),
        ],
        confidence=0.9,
    )
    generic = _strip_unbound(draft, ["15 days"], "The exact figures are in the policy wording.")
    assert generic is not None
    assert "15" not in generic.answer and "Keep your receipts" in generic.answer
    assert len(generic.claims) == 2
    assert generic.answer.endswith("The exact figures are in the policy wording.")


def test_a_span_read_across_a_line_break_drops_both_lines() -> None:
    draft = GroundedAnswer(
        answer="Premium table\n| Sum insured | S$\n3,000 |\nAll other terms apply.",
        claims=[
            Claim(text="Premium table", source_id="p", locator="p"),
            Claim(text="All other terms apply.", source_id="p", locator="p"),
        ],
        confidence=0.9,
    )
    generic = _strip_unbound(draft, ["S$\n3,000"], "pointer")
    assert generic is not None
    assert "3,000" not in generic.answer and "S$" not in generic.answer
    assert "All other terms apply." in generic.answer


def test_nothing_left_means_no_generic_reply() -> None:
    draft = GroundedAnswer(
        answer="Notify us within 15 days.",
        claims=[Claim(text="Notify us within 15 days.", source_id="p", locator="p")],
        confidence=0.9,
    )
    assert _strip_unbound(draft, ["15 days"], "pointer") is None


# --- on the real corpus ----------------------------------------------------------


@real_only
def test_real_claim_steps_are_delivered_without_the_unbound_number() -> None:
    real = Bundle.load(REAL)
    settings = Settings(bundle_path=REAL)
    session = make_session(policy_id=None, today=dt.date(2026, 9, 4))
    env, _ = answer_question(
        real,
        "How do I make a claim on Accident & Health Insurance?",
        session,
        settings,  # type: ignore[arg-type]
    )
    assert env.delivered
    assert any(c.source_id.startswith("product/health-medical/accident-health") for c in env.answer.claims)


@real_only
def test_real_cancellation_and_documents_get_steps_not_a_colleague() -> None:
    real = Bundle.load(REAL)
    settings = Settings(bundle_path=REAL)
    for question, needle in (
        ("I want to cancel Tiq CashSaver", "cancel"),
        ("Where can I find the policy wording for Accident & Health Insurance?", "policy wording"),
    ):
        session = make_session(policy_id=None, today=dt.date(2026, 9, 4))
        env, _ = answer_question(real, question, session, settings)  # type: ignore[arg-type]
        assert env.delivered and env.answer.guidance and env.answer.handoff, question
        assert needle in env.answer.answer.lower()
        assert env.answer.claims and env.answer.claims[0].source_id.startswith("product/")


@real_only
def test_real_document_links_are_the_products_own() -> None:
    real = Bundle.load(REAL)
    links = document_links(real, REAL / "raw", real.get("product/general/travel-infinite"))
    assert links and all("etiqa.com.sg" in link.url for link in links)
    assert all("travel-infinite" in link.label.lower().replace(" ", "-") for link in links)
