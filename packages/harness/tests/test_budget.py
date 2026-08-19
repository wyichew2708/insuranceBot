import pytest
from harness import Budget, BudgetExhausted


def test_pages_budget_is_a_hard_limit() -> None:
    b = Budget(max_pages=2)
    b.charge_page()
    b.charge_page()
    with pytest.raises(BudgetExhausted) as exc:
        b.charge_page()
    assert exc.value.resource == "pages"
    assert b.exhausted_on == "pages"


def test_tool_calls_budget() -> None:
    b = Budget(max_tool_calls=1)
    b.charge_tool()
    with pytest.raises(BudgetExhausted):
        b.charge_tool()


def test_would_exceed_pages_predicts_without_charging() -> None:
    b = Budget(max_pages=1)
    assert not b.would_exceed_pages()
    b.charge_page()
    assert b.would_exceed_pages()
    assert b.pages_loaded == 1


def test_snapshot_exposes_every_dimension() -> None:
    snap = Budget().snapshot()
    assert {"pages_loaded", "tool_calls", "tokens_used", "elapsed_ms", "exhausted_on"} <= set(snap)
