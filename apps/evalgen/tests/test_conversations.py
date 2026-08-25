"""Multi-turn conversations: what only shows up when turns follow each other."""

from __future__ import annotations

import collections
import datetime as dt
from pathlib import Path

import pytest
from api.settings import Settings
from evalgen.conversation_runner import run_conversation, run_suite, summarise
from evalgen.conversations import Conversation, ConversationSuite, Turn, generate
from evalgen.schema import Expectation, SessionSpec

from okf import Bundle

BUNDLE_ROOT = Path(__file__).resolve().parents[3] / "okf"
TODAY = dt.date(2026, 8, 21)


@pytest.fixture(scope="module")
def suite(bundle: Bundle) -> ConversationSuite:
    return generate(bundle, "okf", TODAY, target=100)


@pytest.fixture(scope="module")
def report(bundle: Bundle, suite: ConversationSuite):  # type: ignore[no-untyped-def]
    settings = Settings(bundle_path=BUNDLE_ROOT, llm_provider="deterministic", guardrails="rules")
    return run_suite(bundle, settings, suite)


def test_the_suite_is_a_hundred_conversations(suite: ConversationSuite) -> None:
    assert len(suite.conversations) == 100
    assert suite.total_turns >= 300
    ids = [c.id for c in suite.conversations]
    assert len(ids) == len(set(ids))


def test_it_covers_the_shapes_customers_actually_arrive_in(suite: ConversationSuite) -> None:
    """One archetype repeated a hundred times measures one thing a hundred
    times. The spread is the point."""
    archetypes = {c.archetype for c in suite.conversations}
    assert len(archetypes) >= 7
    kinds = collections.Counter(t.kind for c in suite.conversations for t in c.turns)
    for required in ("opener", "ellipsis", "repeat", "attack", "advice", "correction", "switch"):
        assert kinds[required] > 0, required


def test_a_meaningful_share_of_turns_need_the_ones_before_them(suite: ConversationSuite) -> None:
    """`Session` carries no history, so these turns are the measurement of that
    gap. Too few and the suite is just a slower single-turn suite."""
    turns = [t for c in suite.conversations for t in c.turns]
    contextual = [t for t in turns if t.needs_context]
    assert 0.25 <= len(contextual) / len(turns) <= 0.6


def test_generation_is_deterministic(bundle: Bundle) -> None:
    """Two runs must be comparable, so the same bundle gives the same suite."""
    a = generate(bundle, "okf", TODAY, target=40)
    b = generate(bundle, "okf", TODAY, target=40)
    assert [c.id for c in a.conversations] == [c.id for c in b.conversations]
    assert [t.question for c in a.conversations for t in c.turns] == [
        t.question for c in b.conversations for t in c.turns
    ]


def test_standalone_and_contextual_turns_are_scored_apart(report) -> None:  # type: ignore[no-untyped-def]
    """One blended number would let a strong standalone score hide a weak
    contextual one, which is the whole thing this suite exists to expose."""
    assert report["standalone_turns"] + report["contextual_turns"] == report["turns"]
    assert 0.0 <= report["contextual_turn_pass_rate"] <= 1.0
    assert 0.0 <= report["standalone_turn_pass_rate"] <= 1.0


def test_a_conversation_passes_only_if_every_turn_did(report) -> None:  # type: ignore[no-untyped-def]
    """Customers do not experience turns, they experience conversations. One
    bad turn spoils the exchange, so the conversation rate is never above the
    turn rate."""
    assert report["conversation_pass_rate"] <= report["turn_pass_rate"]


def test_an_attack_holds_and_the_next_turn_still_gets_answered(report) -> None:  # type: ignore[no-untyped-def]
    """Both halves matter. A bot that keeps refusing after one bad turn has
    punished the customer for the attacker."""
    assert report["attacks_total"] > 0
    assert report["attacks_held"] == report["attacks_total"]
    assert report["recovered_after_attack"] == report["recoverable_attacks"]


def test_consistency_compares_the_bound_row_not_every_number(bundle: Bundle) -> None:
    """The composer answers with a whole section, so a correct answer routinely
    carries several figures. Comparing all of them reported a contradiction
    every time the bot gave more context than the question needed — it flagged
    26 of 100 conversations before this was fixed.
    """
    settings = Settings(bundle_path=BUNDLE_ROOT, llm_provider="deterministic", guardrails="rules")
    row = next(r for r in bundle.tables.rows if r.product == "home")
    benefit = row.benefit_code.replace("_", " ")
    attribute = row.attribute.replace("_", " ")
    question = f"What is the {benefit} {attribute} on Home Insurance?"
    convo = Conversation(
        id="c",
        archetype="t",
        session=SessionSpec(auth_level="L0"),
        turns=[
            Turn(
                question=question,
                kind="opener",
                consistency_tag=row.row_id,
                expect=Expectation(expect_delivered=True),
            ),
            Turn(
                question=f"Sorry, what was that {row.attribute.replace('_', ' ')} again?",
                kind="repeat",
                needs_context=True,
                consistency_tag=row.row_id,
                expect=Expectation(expect_delivered=True),
            ),
        ],
    )
    outcome = run_conversation(bundle, settings, convo)
    assert not outcome["contradictions"], outcome["contradictions"]


def test_no_conversation_contradicts_itself(report) -> None:  # type: ignore[no-untyped-def]
    """Two different figures for one fact inside one exchange is worse than
    either being wrong alone — whichever was right, the customer cannot tell."""
    offenders = [r["id"] for r in report["results"] if r["contradictions"]]
    assert not offenders, offenders[:5]


def test_the_summary_reports_both_turn_populations(report) -> None:  # type: ignore[no-untyped-def]
    text = summarise(report)
    assert "standalone turns" in text and "context-dependent" in text
    assert "self-contradictions" in text and "attacks held" in text
