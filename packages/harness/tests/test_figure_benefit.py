"""A bound figure must belong to the benefit the question asked about."""

from __future__ import annotations

from harness import Claim, Figure, GroundedAnswer, Session
from harness.gates import GateContext, gate_answerability

from okf import Bundle


def _ctx(bundle: Bundle, question: str, row_id: str) -> GateContext:
    answer = GroundedAnswer(
        answer="The amount payable is S$150,000.",
        claims=[Claim(text="The amount payable is S$150,000.", source_id="product/general/travel/benefits")],
        figures=[Figure(label="limit", text="S$150,000", table_row_id=row_id)],
    )
    return GateContext(
        answer=answer,
        bundle=bundle,
        session=Session(session_id="t"),
        question=question,
        loaded_page_ids=["product/general/travel/benefits"],
    )


def test_a_figure_from_another_benefits_row_does_not_answer_a_named_benefit(bundle: Bundle) -> None:
    ctx = _ctx(bundle, "Is there a limit on section 6 under this plan?", "travel:2026:gold:section_1.limit")
    result = gate_answerability(ctx)
    assert result.blocking, result.detail
    assert "section_6" in result.detail and "section_1" in result.detail


def test_a_figure_from_the_named_benefits_row_passes(bundle: Bundle) -> None:
    ctx = _ctx(bundle, "Is there a limit on section 6 under this plan?", "travel:2026:gold:section_6.limit")
    assert not gate_answerability(ctx).blocking


def test_a_question_naming_no_benefit_keeps_the_old_rule(bundle: Bundle) -> None:
    ctx = _ctx(bundle, "how much does it pay out", "travel:2026:gold:section_1.limit")
    assert not gate_answerability(ctx).blocking
