"""Every gate, pass and fail. These are the checks that stand between a
generated draft and a customer, so each one is pinned by a test."""

import datetime as dt

from harness import (
    AuthLevel,
    Channel,
    ChannelRender,
    Claim,
    Figure,
    GateContext,
    GroundedAnswer,
    PolicyContext,
    Session,
    Verdict,
    blocked,
    run_gates,
)
from harness.gates import (
    gate_advice_boundary,
    gate_channel_coherence,
    gate_exclusion_completeness,
    gate_groundedness,
    gate_numeric_binding,
    gate_reference_integrity,
    gate_version_coherence,
)

from okf import Bundle

TODAY = dt.date(2026, 8, 19)
TRAVEL = "product/general/travel"
BENEFITS = "product/general/travel/benefits"
EXCLUSIONS = "product/general/travel/exclusions"


def session(policy: PolicyContext | None = None, channel: Channel = Channel.tiq_sg) -> Session:
    return Session(
        session_id="t",
        channel=channel,
        auth_level=AuthLevel.authenticated,
        today=TODAY,
        policy=policy
        or PolicyContext(policy_id="TRV-100001", product_id=TRAVEL, version="2026.1", tier="tier-2"),
    )


def ctx(
    bundle: Bundle,
    answer: GroundedAnswer,
    loaded: list[str] | None = None,
    session_override: Session | None = None,
    question: str = "what is the limit?",
) -> GateContext:
    return GateContext(
        answer=answer,
        bundle=bundle,
        session=session_override or session(),
        question=question,
        loaded_page_ids=loaded or [TRAVEL, BENEFITS, EXCLUSIONS],
        raw_root=bundle.root / "raw",
        today=TODAY,
    )


# --- reference integrity ---


def test_reference_integrity_passes_for_approved_pages(bundle: Bundle) -> None:
    a = GroundedAnswer(answer="x", claims=[Claim(text="c", source_id=BENEFITS)])
    assert gate_reference_integrity(ctx(bundle, a)).verdict is Verdict.pass_


def test_reference_integrity_rejects_unknown_source(bundle: Bundle) -> None:
    a = GroundedAnswer(answer="x", claims=[Claim(text="c", source_id="product/does/not/exist")])
    assert gate_reference_integrity(ctx(bundle, a)).verdict is Verdict.fail


def test_factual_answer_without_claims_fails(bundle: Bundle) -> None:
    assert gate_reference_integrity(ctx(bundle, GroundedAnswer(answer="x"))).verdict is Verdict.fail


def test_handoff_needs_no_claims(bundle: Bundle) -> None:
    a = GroundedAnswer(answer="passing you over", handoff=True)
    assert gate_reference_integrity(ctx(bundle, a)).verdict is Verdict.skip


# --- numeric binding ---


def test_bound_figure_passes(bundle: Bundle) -> None:
    a = GroundedAnswer(
        answer="The limit is S$500,000.",
        figures=[
            Figure(label="l", text="S$500,000", table_row_id="travel:2026.1:tier-2:medical_expenses.limit")
        ],
    )
    assert gate_numeric_binding(ctx(bundle, a)).verdict is Verdict.pass_


def test_unbound_figure_is_blocked_outright(bundle: Bundle) -> None:
    a = GroundedAnswer(answer="The limit is S$500,000.", figures=[Figure(label="l", text="S$500,000")])
    assert gate_numeric_binding(ctx(bundle, a)).verdict is Verdict.fail


def test_number_in_prose_without_a_figure_is_blocked(bundle: Bundle) -> None:
    # The classic hallucination: a number nobody fetched.
    a = GroundedAnswer(answer="The delay benefit starts after 4 hours.")
    result = gate_numeric_binding(ctx(bundle, a))
    assert result.verdict is Verdict.fail
    assert "4 hours" in result.detail


def test_rendered_hotline_digits_are_bound_by_construction(bundle: Bundle) -> None:
    a = GroundedAnswer(
        answer="Call +65 6887 8777 for help.",
        channel_render=ChannelRender(channel=Channel.tiq_sg, hotline="+65 6887 8777"),
    )
    assert gate_numeric_binding(ctx(bundle, a)).verdict is Verdict.pass_


def test_promotion_figure_may_bind_to_an_in_window_promo_page(bundle: Bundle) -> None:
    a = GroundedAnswer(
        answer="A discount of 15% applies.",
        figures=[Figure(label="promotion", text="15%", page_ref="promotion/travel-aug-2026")],
    )
    assert gate_numeric_binding(ctx(bundle, a)).verdict is Verdict.pass_


def test_promotion_figure_from_an_expired_page_is_blocked(bundle: Bundle) -> None:
    a = GroundedAnswer(
        answer="A discount of 10% applies.",
        figures=[Figure(label="promotion", text="10%", page_ref="promotion/travel-jun-2026")],
    )
    assert gate_numeric_binding(ctx(bundle, a)).verdict is Verdict.fail


def test_page_ref_binding_cannot_launder_a_product_page(bundle: Bundle) -> None:
    a = GroundedAnswer(answer="99%", figures=[Figure(label="x", text="99%", page_ref=TRAVEL)])
    assert gate_numeric_binding(ctx(bundle, a)).verdict is Verdict.fail


# --- version coherence ---


def test_version_matching_the_policy_passes(bundle: Bundle) -> None:
    a = GroundedAnswer(answer="x", claims=[Claim(text="c", source_id=BENEFITS)])
    assert gate_version_coherence(ctx(bundle, a)).verdict is Verdict.pass_


def test_historic_policy_version_blocks_a_current_page_answer(bundle: Bundle) -> None:
    old = session(
        policy=PolicyContext(policy_id="TRV-900001", product_id=TRAVEL, version="2025.2", tier="tier-2")
    )
    a = GroundedAnswer(answer="x", claims=[Claim(text="c", source_id=BENEFITS)])
    assert gate_version_coherence(ctx(bundle, a, session_override=old)).verdict is Verdict.fail


# --- channel coherence ---


def test_channel_render_must_match_the_session(bundle: Bundle) -> None:
    a = GroundedAnswer(answer="x", channel_render=ChannelRender(channel=Channel.etiqa_sg))
    assert gate_channel_coherence(ctx(bundle, a)).verdict is Verdict.fail


def test_answer_leaking_the_other_channels_hotline_is_blocked(bundle: Bundle) -> None:
    a = GroundedAnswer(
        answer="Call +65 6336 0477.",
        channel_render=ChannelRender(channel=Channel.tiq_sg, hotline="+65 6887 8777"),
    )
    assert gate_channel_coherence(ctx(bundle, a)).verdict is Verdict.fail


# --- exclusion completeness ---


def test_coverage_claim_without_reading_exclusions_is_blocked(bundle: Bundle) -> None:
    a = GroundedAnswer(answer="Baggage loss is covered.")
    result = gate_exclusion_completeness(ctx(bundle, a, loaded=[TRAVEL, BENEFITS]))
    assert result.verdict is Verdict.fail


def test_coverage_claim_with_exclusions_read_passes(bundle: Bundle) -> None:
    a = GroundedAnswer(answer="Baggage loss is covered.")
    assert gate_exclusion_completeness(ctx(bundle, a)).verdict is Verdict.pass_


def test_no_coverage_assertion_skips(bundle: Bundle) -> None:
    a = GroundedAnswer(answer="Here is how to make a claim.")
    assert gate_exclusion_completeness(ctx(bundle, a, loaded=[TRAVEL])).verdict is Verdict.skip


# --- advice boundary ---


def test_advice_seeking_question_requires_the_flag(bundle: Bundle) -> None:
    a = GroundedAnswer(answer="Buy the top plan.")
    result = gate_advice_boundary(ctx(bundle, a, question="which plan should I buy?"))
    assert result.verdict is Verdict.fail


def test_advice_seeking_with_flag_set_passes(bundle: Bundle) -> None:
    a = GroundedAnswer(answer="I'll connect you with an adviser.", advice_flag=True)
    assert gate_advice_boundary(ctx(bundle, a, question="which plan should I buy?")).verdict is Verdict.pass_


# --- groundedness ---


def test_claim_entailed_by_loaded_pages_passes(bundle: Bundle) -> None:
    a = GroundedAnswer(
        answer="x",
        claims=[
            Claim(
                text="No benefit is payable for pre-existing medical conditions unless the "
                "optional extension was effected at policy inception.",
                source_id=EXCLUSIONS,
            )
        ],
    )
    assert gate_groundedness(ctx(bundle, a)).verdict is Verdict.pass_


def test_unentailed_claim_is_blocked(bundle: Bundle) -> None:
    a = GroundedAnswer(
        answer="x", claims=[Claim(text="Skydiving in Antarctica is fully reimbursed", source_id=EXCLUSIONS)]
    )
    assert gate_groundedness(ctx(bundle, a)).verdict is Verdict.fail


# --- suite ---


def test_all_seven_gates_run(bundle: Bundle) -> None:
    results = run_gates(ctx(bundle, GroundedAnswer(answer="x", handoff=True)))
    assert len(results) == 7
    assert {r.gate for r in results} == {
        "reference-integrity",
        "numeric-binding",
        "version-coherence",
        "channel-coherence",
        "exclusion-completeness",
        "advice-boundary",
        "groundedness",
    }


def test_blocked_reports_any_failure(bundle: Bundle) -> None:
    a = GroundedAnswer(answer="The limit is S$999,999.", figures=[Figure(label="l", text="S$999,999")])
    assert blocked(run_gates(ctx(bundle, a)))
