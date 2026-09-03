"""Every gate, pass and fail. These are the checks that stand between a
generated draft and a customer, so each one is pinned by a test."""

import datetime as dt
from pathlib import Path

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
    ALL_GATES,
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


def session(policy: PolicyContext | None = None, channel: Channel = Channel.direct) -> Session:
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


def test_a_quoted_contract_figure_is_bound_by_transcription(bundle: Bundle, tmp_path: Path) -> None:
    """A wording's numbers cannot become benefit-table rows — a notice period
    is not a benefit — and cannot be paraphrased without changing what was
    agreed. Quoting binds them, and the gate checks the quotation."""
    (tmp_path / "wordings").mkdir(parents=True)
    (tmp_path / "wordings" / "travel.md").write_text(
        "You must notify Us within thirty (30) days of the event."
    )
    a = GroundedAnswer(
        answer="You must notify us within thirty (30) days.",
        figures=[Figure(label="quotation", text="30", quote_ref="raw/wordings/travel.md#p3")],
    )
    context = ctx(bundle, a)
    context.raw_root = tmp_path
    assert gate_numeric_binding(context).verdict is Verdict.pass_


def test_a_quotation_the_source_does_not_contain_is_blocked(bundle: Bundle, tmp_path: Path) -> None:
    """Otherwise `quote_ref` would be a way to assert any number at all."""
    (tmp_path / "wordings").mkdir(parents=True)
    (tmp_path / "wordings" / "travel.md").write_text("You must notify Us within thirty (30) days.")
    a = GroundedAnswer(
        answer="You must notify us within 90 days.",
        figures=[Figure(label="quotation", text="90", quote_ref="raw/wordings/travel.md#p3")],
    )
    context = ctx(bundle, a)
    context.raw_root = tmp_path
    result = gate_numeric_binding(context)
    assert result.verdict is Verdict.fail
    assert "does not contain it" in result.detail


def test_a_quotation_is_matched_past_the_extractor_punctuation(bundle: Bundle, tmp_path: Path) -> None:
    """An extractor writes `S$ 1,000` where the PDF printed `S$1,000`."""
    (tmp_path / "wordings").mkdir(parents=True)
    (tmp_path / "wordings" / "travel.md").write_text("the excess is S$ 1,000 per claim")
    a = GroundedAnswer(
        answer="The excess is S$1,000.",
        figures=[Figure(label="quotation", text="S$1,000", quote_ref="raw/wordings/travel.md")],
    )
    context = ctx(bundle, a)
    context.raw_root = tmp_path
    assert gate_numeric_binding(context).verdict is Verdict.pass_


def test_a_quotation_with_nothing_to_check_against_is_not_bound(bundle: Bundle) -> None:
    """An unverifiable claim of verbatimness is not a binding."""
    a = GroundedAnswer(
        answer="You must notify us within 30 days.",
        figures=[Figure(label="quotation", text="30", quote_ref="raw/wordings/travel.md")],
    )
    context = ctx(bundle, a)
    context.raw_root = None
    assert gate_numeric_binding(context).verdict is Verdict.fail


def test_rendered_hotline_digits_are_bound_by_construction(bundle: Bundle) -> None:
    a = GroundedAnswer(
        answer="Call +65 6887 8777 for help.",
        channel_render=ChannelRender(channel=Channel.direct, hotline="+65 6887 8777"),
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
    a = GroundedAnswer(answer="x", channel_render=ChannelRender(channel=Channel.agency))
    assert gate_channel_coherence(ctx(bundle, a)).verdict is Verdict.fail


def test_either_front_door_of_the_direct_channel_is_fine(bundle: Bundle) -> None:
    """etiqa.com.sg and tiq.com.sg are the same channel, not rival brands.

    Citing one while the render names the other used to be a leak. It is not:
    the customer starts from the product and never has to know which address
    they arrived through.
    """
    for landing in ("https://www.etiqa.com.sg/", "https://www.tiq.com.sg/"):
        a = GroundedAnswer(
            answer=f"You can continue here: {landing} or call +65 6887 8777.",
            channel_render=ChannelRender(
                channel=Channel.direct,
                landing="https://www.etiqa.com.sg/",
                hotline="+65 6336 0477",
                surfaces=["https://www.tiq.com.sg/"],
            ),
        )
        assert gate_channel_coherence(ctx(bundle, a)).verdict is Verdict.pass_


def test_answer_offering_another_distribution_route_is_blocked(bundle: Bundle) -> None:
    """Distribution routes are still distinct: a direct customer must not be
    handed the agency route, which is a different way to buy."""
    a = GroundedAnswer(
        answer="Find an agent at https://www.etiqa.com.sg/find-an-agent/.",
        channel_render=ChannelRender(channel=Channel.direct, hotline="+65 6336 0477"),
    )
    assert gate_channel_coherence(ctx(bundle, a)).verdict is Verdict.fail


def test_shared_corporate_hotline_is_not_a_leak(bundle: Bundle) -> None:
    """Every route publishes the same corporate number; a shared value is not
    evidence that the answer wandered into another channel."""
    a = GroundedAnswer(
        answer="Call +65 6336 0477.",
        channel_render=ChannelRender(channel=Channel.agency, hotline="+65 6336 0477"),
    )
    result = gate_channel_coherence(ctx(bundle, a, session_override=session(channel=Channel.agency)))
    assert result.verdict is Verdict.pass_


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


def test_every_gate_runs_regardless_of_earlier_failures(bundle: Bundle) -> None:
    """The console shows the full picture, so a failure never short-circuits the
    rest. Counted from `ALL_GATES` rather than written down: the number has
    changed twice — `answerability` was the first to compare the answer to the
    question, and `entitlement-assertion` the first to weigh it against who is
    asking — and a hardcoded count only records when someone last edited it."""
    results = run_gates(ctx(bundle, GroundedAnswer(answer="x", handoff=True)))
    assert len(results) == len(ALL_GATES)
    assert {r.gate for r in results} == {
        "entitlement-assertion",
        "about-the-ask",
        "supporting-sources",
        "reference-integrity",
        "numeric-binding",
        "version-coherence",
        "channel-coherence",
        "exclusion-completeness",
        "advice-boundary",
        "groundedness",
        "answerability",
    }


def test_blocked_reports_any_failure(bundle: Bundle) -> None:
    a = GroundedAnswer(answer="The limit is S$999,999.", figures=[Figure(label="l", text="S$999,999")])
    assert blocked(run_gates(ctx(bundle, a)))


# --- smalltalk ---


def test_a_greeting_is_not_a_factual_answer(bundle: Bundle) -> None:
    """ "hi" carries no claims and asserts nothing. Without this the provenance
    gates read it as a factual answer with no sources and refuse it, and the
    customer who said hello is told we are passing them to a colleague."""
    a = GroundedAnswer(answer="Hello. What would you like to know?", smalltalk=True)
    results = {g.gate: g.verdict for g in run_gates(ctx(bundle, a, loaded=[], question="hi"))}
    assert Verdict.fail not in results.values(), results
    assert results["reference-integrity"] is Verdict.skip
    assert results["groundedness"] is Verdict.skip
    assert results["answerability"] is Verdict.skip


def test_smalltalk_does_not_excuse_an_unbound_number(bundle: Bundle) -> None:
    """The flag says the turn asserted nothing, not that anything goes. A
    figure in a greeting is still a figure nobody fetched."""
    a = GroundedAnswer(answer="Hello — your limit is S$500,000.", smalltalk=True)
    assert gate_numeric_binding(ctx(bundle, a, loaded=[])).verdict is Verdict.fail
