"""End-to-end serve loop (Loop 1)."""

import datetime as dt

from api.pipeline import answer_question
from api.settings import Settings
from harness import AuthLevel, Channel

from conftest import make_session
from okf import Bundle


def ask(bundle: Bundle, settings: Settings, question: str, **kw: object):  # type: ignore[no-untyped-def]
    return answer_question(bundle, question, make_session(**kw), settings)  # type: ignore[arg-type]


def test_figures_are_bound_to_table_rows(bundle: Bundle, settings: Settings) -> None:
    env, _ = ask(bundle, settings, "What is the overseas medical expenses limit?")
    assert env.delivered
    assert env.answer.figures
    assert all(f.table_row_id for f in env.answer.figures)
    assert "S$500,000" in env.answer.answer  # tier-2 row, not tier-1 or tier-3


def test_tier_comes_from_the_system_of_record(bundle: Bundle, settings: Settings) -> None:
    tier2, _ = ask(bundle, settings, "What is the overseas medical expenses limit?")
    tier3, _ = ask(
        bundle,
        settings,
        "What is the overseas medical expenses limit?",
        policy_id="TRV-100002",
        tier="tier-3",
    )
    assert "S$500,000" in tier2.answer.answer
    assert "S$1,000,000" in tier3.answer.answer


def test_anonymous_session_will_not_guess_a_tier(bundle: Bundle, settings: Settings) -> None:
    env, _ = ask(
        bundle,
        settings,
        "What is the overseas medical expenses limit?",
        auth=AuthLevel.anonymous,
        policy_id=None,
    )
    assert env.answer.unresolved, "an unknown tier must be declared, not guessed"
    assert "S$500,000" not in env.answer.answer


def test_channel_render_is_deterministic(bundle: Bundle, settings: Settings) -> None:
    tiq, _ = ask(bundle, settings, "How do I buy travel insurance?")
    etiqa, _ = ask(bundle, settings, "How do I buy travel insurance?", channel=Channel.etiqa_sg)
    assert tiq.answer.channel_render is not None and etiqa.answer.channel_render is not None
    assert tiq.answer.channel_render.brand == "Tiq"
    assert etiqa.answer.channel_render.brand == "Etiqa"
    assert tiq.answer.channel_render.landing != etiqa.answer.channel_render.landing


def test_unknown_channel_offers_both_routes(bundle: Bundle, settings: Settings) -> None:
    env, _ = ask(bundle, settings, "How do I buy travel insurance?", channel=Channel.unknown)
    assert env.answer.channel_render is not None
    assert env.answer.channel_render.both_shown


def test_historic_policy_version_is_blocked_not_answered(bundle: Bundle, settings: Settings) -> None:
    env, trace = ask(
        bundle,
        settings,
        "How long before the delay benefit applies?",
        policy_id="TRV-900001",
        version="2025.2",
    )
    assert not env.delivered
    assert any(g.gate == "version-coherence" and g.blocking for g in env.gates)
    assert trace.rag_used and "historic version" in trace.rag_reason
    assert trace.blocked_draft, "the blocked draft must be kept for debugging"


def test_advice_question_flags_and_routes(bundle: Bundle, settings: Settings) -> None:
    env, _ = ask(bundle, settings, "Which travel plan should I buy?")
    assert env.answer.advice_flag
    assert "adviser" in env.answer.answer.lower()


def test_injection_planted_in_crawled_copy_is_not_obeyed(bundle: Bundle, settings: Settings) -> None:
    env, _ = ask(bundle, settings, "What does the website copy say about the policy wording section?")
    assert "unlimited" not in env.answer.answer.lower()
    assert "ignore all previous instructions" not in env.answer.answer.lower()


def test_every_stage_is_traced(bundle: Bundle, settings: Settings) -> None:
    _, trace = ask(bundle, settings, "What is the baggage limit?")
    names = [s.name for s in trace.stages]
    assert names == ["frontmatter-filter", "wiki-read", "rag-decision", "sor", "compose", "gates"]
    assert trace.candidates and trace.rejected
    assert trace.budget["pages_loaded"] > 0
    assert trace.answer is not None


def test_stale_bundle_hands_off_rather_than_answering(bundle: Bundle, settings: Settings) -> None:
    env, _ = ask(bundle, settings, "What is the overseas medical expenses limit?", today=dt.date(2026, 12, 1))
    assert env.answer.handoff


def test_exclusion_page_is_loaded_before_asserting_coverage(bundle: Bundle, settings: Settings) -> None:
    _, trace = ask(bundle, settings, "Is baggage loss covered on travel insurance?")
    loaded = {p.page_id for p in trace.loaded}
    assert "product/general/travel/exclusions" in loaded
