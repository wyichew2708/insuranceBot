"""Carrying the topic between turns.

From a reported conversation: "term life", then "what's the coverages". The
second turn is about term life and says so nowhere — it was retrieved on four
words that name no product, and refused.
"""

from __future__ import annotations

from api.pipeline import answer_question
from api.reference import names_a_subject, resolve
from api.settings import Settings

from conftest import make_session
from okf import Bundle


def test_a_turn_that_names_nothing_borrows_the_topic(bundle: Bundle) -> None:
    out = resolve("whats the coverages", ["travel insurance"], bundle)
    assert out.resolved
    assert out.carried_from == "travel insurance"
    # Prepended, not substituted: the topic picks the product and the
    # customer's own words still pick the section.
    assert out.question.endswith("whats the coverages")
    assert "travel" in out.question


def test_a_turn_that_names_its_own_subject_is_left_alone(bundle: Bundle) -> None:
    """Carrying the topic into every turn is how "what about car insurance?"
    gets answered about term life."""
    out = resolve("what does home insurance cover", ["travel insurance"], bundle)
    assert not out.resolved
    assert out.question == "what does home insurance cover"


def test_the_subject_is_checked_against_the_corpus(bundle: Bundle) -> None:
    """Not a grammatical test — "coverages" is a noun and names no product."""
    assert names_a_subject("what does travel insurance cover", bundle)
    assert not names_a_subject("whats the coverages", bundle)
    assert not names_a_subject("what about that", bundle)


def test_no_history_is_a_working_state(bundle: Bundle) -> None:
    out = resolve("whats the coverages", [], bundle)
    assert not out.resolved
    assert out.question == "whats the coverages"


def test_the_nearest_naming_turn_wins(bundle: Bundle) -> None:
    out = resolve("and the exclusions", ["travel insurance", "ok", "thanks"], bundle)
    assert out.carried_from == "travel insurance"


def test_an_elliptical_follow_up_is_answered(bundle: Bundle, settings: Settings) -> None:
    env, trace = answer_question(
        bundle,
        "whats the coverages",
        make_session(),
        settings,
        history=["travel insurance"],
    )
    assert not env.answer.handoff, env.answer.answer
    assert env.answer.claims
    carried = next(s for s in trace.stages if s.name == "reference").detail
    assert carried["carried_from"] == "travel insurance"


def test_the_same_turn_without_history_still_refuses(bundle: Bundle, settings: Settings) -> None:
    """The resolution is what changed the outcome, not a loosened floor."""
    env, _ = answer_question(bundle, "whats the coverages", make_session(), settings, history=[])
    assert env.answer.handoff
