"""Comparisons bind every cell, preserve advice routing, and refuse absent tiers."""

from dataclasses import replace
from pathlib import Path

import pytest
from api.pipeline import answer_question
from api.settings import Settings
from harness import Channel, GateContext, Session
from harness.gates import gate_numeric_binding
from okf.tables import BenefitTables

from okf import Bundle


@pytest.mark.parametrize(
    "question",
    [
        "Compare Home Insurance with Private Car Insurance",
        "Compare tier-1 with tier-2 for Travel Insurance",
    ],
)
def test_comparison_is_a_bound_table(bundle: Bundle, settings: Settings, question: str) -> None:
    envelope, trace = answer_question(bundle, question, Session(session_id="compare"), settings)
    assert envelope.delivered, envelope.gates
    table = envelope.answer.table
    assert table is not None and table.rows
    assert trace.handler == "knowledge.compare"
    indexed = {r.row_id: r for r in bundle.tables.rows}
    for row in table.rows:
        for cell in row.cells:
            if cell:
                assert cell.table_row_id in indexed
                assert cell.text == indexed[cell.table_row_id].rendered()
    assert any(g.gate == "guardrail-output" for g in envelope.gates)


def test_table_gate_rejects_a_changed_cell(bundle: Bundle, settings: Settings) -> None:
    envelope, _ = answer_question(
        bundle, "Compare Home Insurance with Private Car Insurance", Session(session_id="tamper"), settings
    )
    answer = envelope.answer.model_copy(deep=True)
    assert answer.table is not None
    cell = next(c for r in answer.table.rows for c in r.cells if c)
    for row in answer.table.rows:
        row.cells = [c.model_copy(update={"text": "S$999,999"}) if c == cell else c for c in row.cells]
    result = gate_numeric_binding(
        GateContext(answer=answer, bundle=bundle, session=Session(session_id="gate"))
    )
    assert result.blocking


def test_missing_tables_are_named_and_linked(bundle: Bundle, settings: Settings) -> None:
    empty = replace(bundle, tables=BenefitTables([]))
    envelope, _ = answer_question(
        empty,
        "Compare Home Insurance with Private Car Insurance",
        Session(session_id="missing", channel=Channel.direct),
        settings,
    )
    assert envelope.delivered
    assert envelope.answer.table is None
    assert "do not have a published benefit table" in envelope.answer.answer
    assert len(envelope.answer.destinations) == 2
    assert not envelope.answer.figures


def test_comparison_does_not_choose_tiers_or_recommend(bundle: Bundle, settings: Settings) -> None:
    session = Session(session_id="choices")
    ambiguous, _ = answer_question(bundle, "Compare Travel Insurance with Home Insurance", session, settings)
    assert ambiguous.delivered and not ambiguous.answer.clarifying
    assert ambiguous.answer.table is None
    assert "product-level comparison" in ambiguous.answer.answer
    two_names, _ = answer_question(bundle, "Home Insurance and Private Car Insurance", session, settings)
    assert two_names.answer.clarifying and two_names.answer.table is None
    advice, trace = answer_question(
        bundle, "Compare Home Insurance and Private Car Insurance and recommend one", session, settings
    )
    assert trace.handler == "advice"
    assert advice.answer.advice_flag and advice.answer.table is None


def test_conceptual_difference_is_not_a_two_product_comparison() -> None:
    bundle = Bundle.load(Path("okf-real"))
    _, trace = answer_question(
        bundle,
        "What is the difference between comprehensive and third-party motor cover?",
        Session(session_id="concept"),
        Settings(bundle_path=bundle.root),
    )
    assert trace.handler != "knowledge.compare"
