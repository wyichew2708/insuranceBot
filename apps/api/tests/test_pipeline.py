"""End-to-end serve loop (Loop 1)."""

import datetime as dt

from api.pipeline import answer_question
from api.settings import Settings
from harness import AuthLevel, Channel, Verdict

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
    """The route is resolved from the session, not chosen by the model — and
    what differs between routes is how you buy, never the product."""
    direct, _ = ask(bundle, settings, "How do I buy travel insurance?")
    agency, _ = ask(bundle, settings, "How do I buy travel insurance?", channel=Channel.agency)
    assert direct.answer.channel_render is not None and agency.answer.channel_render is not None
    assert direct.answer.channel_render.name == "Direct"
    assert agency.answer.channel_render.name == "Agency"
    assert direct.answer.channel_render.landing != agency.answer.channel_render.landing


def test_the_two_direct_front_doors_are_one_route(bundle: Bundle, settings: Settings) -> None:
    """A direct session resolves to one route that carries both addresses; the
    customer is never asked to pick a brand."""
    env, _ = ask(bundle, settings, "How do I buy travel insurance?")
    render = env.answer.channel_render
    assert render is not None
    assert render.channel is Channel.direct
    hosts = {u.split("/")[2] for u in [render.landing or "", *render.surfaces] if u}
    assert hosts == {"www.etiqa.com.sg", "www.tiq.com.sg"}


def test_unknown_channel_offers_both_routes(bundle: Bundle, settings: Settings) -> None:
    env, _ = ask(bundle, settings, "How do I buy travel insurance?", channel=Channel.unknown)
    assert env.answer.channel_render is not None
    assert env.answer.channel_render.all_routes_shown


# The routes travel.md declares in its "How to buy" channel-variant block.
DIRECT_ROUTE = "https://www.etiqa.com.sg/personal/travel-insurance/"
TIQ_ROUTE = "https://www.tiq.com.sg/product/travel-insurance/"
AGENCY_ROUTE = "https://www.etiqa.com.sg/find-an-agent/"
BUY = "Where do I buy travel insurance?"


def test_channel_tokens_never_reach_the_customer(bundle: Bundle, settings: Settings) -> None:
    """A channel-variant block is machine markup. Neither the `{{channel.*}}`
    token nor the table it is authored in is customer copy, on any route."""
    for channel in (Channel.direct, Channel.agency, Channel.broker, Channel.unknown):
        env, _ = ask(bundle, settings, BUY, channel=channel)
        answer = env.answer.answer
        assert "{{" not in answer, f"unresolved token delivered on {channel.value}"
        assert "|" not in answer, f"raw markdown table delivered on {channel.value}"
        assert not env.answer.unresolved


def test_how_to_buy_offers_only_the_session_route(bundle: Bundle, settings: Settings) -> None:
    """The block declares every route the product is sold through; the answer
    renders the one this session is actually on. Offering a second route's
    contact is what the channel-coherence gate exists to stop."""
    direct, _ = ask(bundle, settings, BUY)
    agency, _ = ask(bundle, settings, BUY, channel=Channel.agency)
    assert DIRECT_ROUTE in direct.answer.answer
    assert AGENCY_ROUTE not in direct.answer.answer
    assert AGENCY_ROUTE in agency.answer.answer
    assert DIRECT_ROUTE not in agency.answer.answer


def test_both_direct_front_doors_render_as_one_route(bundle: Bundle, settings: Settings) -> None:
    """The direct binding carries two addresses. They are surfaces of one
    route, so both are rendered rather than posed as a choice of brand."""
    env, _ = ask(bundle, settings, BUY)
    assert DIRECT_ROUTE in env.answer.answer
    assert TIQ_ROUTE in env.answer.answer


def test_unknown_channel_renders_every_declared_route(bundle: Bundle, settings: Settings) -> None:
    """No route in the session means no route to guess, so the block offers
    all of them (§C.4) rather than picking one."""
    env, _ = ask(bundle, settings, BUY, channel=Channel.unknown)
    assert DIRECT_ROUTE in env.answer.answer
    assert AGENCY_ROUTE in env.answer.answer


def test_a_route_the_page_does_not_declare_falls_back_to_the_registry(
    bundle: Bundle, settings: Settings
) -> None:
    """travel.md is sold direct and through agents. A broker session is sent to
    its own route from the channel registry, never down someone else's."""
    env, _ = ask(bundle, settings, BUY, channel=Channel.broker)
    assert env.delivered
    assert DIRECT_ROUTE not in env.answer.answer
    assert AGENCY_ROUTE not in env.answer.answer
    assert "https://www.etiqa.com.sg/broker/" in env.answer.answer


def test_substituted_contacts_are_bound_numbers(bundle: Bundle, settings: Settings) -> None:
    """Digits inside a rendered contact came from the channel page, not the
    model, so the numeric-binding gate must treat them as bound (§F.2)."""
    env, _ = ask(bundle, settings, "How do I reach the direct channel?")
    page = bundle.get("channel/direct")
    assert page is not None
    declared = (page.frontmatter.model_extra or {})["hotline"]
    assert declared in env.answer.answer
    numeric = next(g for g in env.gates if g.gate == "numeric-binding")
    assert numeric.verdict is Verdict.pass_, numeric.detail
    assert env.delivered


def test_the_compiled_page_outranks_the_registry_constant(bundle: Bundle, settings: Settings) -> None:
    """The channel page is compiled from the website; the registry in
    okf.channels is only a fallback. A hotline that changes on the site must
    not be answered from a number baked into the code."""
    page = bundle.get("channel/direct")
    assert page is not None
    extra = page.frontmatter.model_extra or {}
    env, _ = ask(bundle, settings, "How do I reach the direct channel?")
    assert str(extra["hotline"]) in env.answer.answer
    # Both front doors the page declares are still offered.
    assert str(extra["landing"]) in env.answer.answer
    for surface in extra.get("surfaces", []):
        assert str(surface) in env.answer.answer


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
    assert names == [
        # Screening brackets the loop: nothing is retrieved for a turn that
        # will be refused, and nothing ships without the drafted text itself
        # having been read.
        "guardrail-input",
        # What "it" refers to: a turn naming no subject borrows the topic from
        # the nearest earlier turn that did.
        "reference",
        # Initials are spelled out before anything scores the words: the
        # tokeniser drops anything under three characters, so "ci" reached
        # retrieval as nothing at all.
        "expand",
        "frontmatter-filter",
        "wiki-read",
        "rag-decision",
        "sor",
        "compose",
        "generate",
        "guardrail-output",
        "gates",
    ]
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


# --- smalltalk ---


def test_a_greeting_is_answered_as_one(bundle: Bundle, settings: Settings) -> None:
    """Reported from the chat surface: saying "hi" returned "I could not
    establish that from our approved product pages. Let me pass you to a
    colleague." A greeting is not a question the corpus can fail to answer."""
    env, _ = ask(bundle, settings, "hi")
    assert env.delivered
    assert env.answer.smalltalk
    assert not env.answer.handoff
    assert "could not establish" not in env.answer.answer
    assert env.answer.answer.startswith("Hello")
    # The underwriter comes from the bundle it is serving, not a constant.
    assert bundle.manifest.underwriter.rstrip(".") in env.answer.answer


def test_a_greeting_costs_no_retrieval(bundle: Bundle, settings: Settings) -> None:
    """It short-circuits after screening, so no page budget, no SOR call and
    no model call are spent discovering that "hi" is not about insurance."""
    _, trace = ask(bundle, settings, "hello")
    stages = [s.name for s in trace.stages]
    assert "smalltalk" in stages
    assert "wiki-read" not in stages
    assert "compose" not in stages
    assert not trace.loaded
    assert not trace.candidates


def test_a_greeting_is_still_gated(bundle: Bundle, settings: Settings) -> None:
    """Every gate runs and every one skips. A turn that silently bypassed
    verification would look identical, on the trace, to one that passed it."""
    env, _ = ask(bundle, settings, "thanks")
    assert env.gates, "the gates must run, not be skipped over"
    assert not [g for g in env.gates if g.verdict is Verdict.fail]


def test_a_greeting_with_a_question_attached_is_a_question(bundle: Bundle, settings: Settings) -> None:
    env, trace = ask(bundle, settings, "hi, what is the overseas medical expenses limit?")
    assert not env.answer.smalltalk
    assert "smalltalk" not in [s.name for s in trace.stages]
    assert env.answer.figures
