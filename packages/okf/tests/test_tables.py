import pytest
from okf.tables import BenefitTables, MissingRow, TableRow, find_tokens, resolve_transclusions

ROWS = [
    TableRow("travel", "2026.1", "ALL", "travel_delay", "threshold_hours", "6", "hours", "raw/ps.md#d"),
    TableRow("travel", "2026.1", "tier-2", "travel_delay", "payout_per_block", "150", "S$", "raw/ps.md#d"),
    TableRow("travel", "2026.1", "tier-3", "travel_delay", "payout_per_block", "200", "S$", "raw/ps.md#d"),
    TableRow("travel", "2026.1", "tier-2", "medical_expenses", "limit", "500000", "S$", "raw/ps.md#m"),
]
TABLES = BenefitTables(ROWS)


def test_row_id_is_fully_qualified() -> None:
    assert ROWS[1].row_id == "travel:2026.1:tier-2:travel_delay.payout_per_block"


def test_rendering_formats_thousands_and_units() -> None:
    assert ROWS[3].rendered() == "S$500,000"
    assert ROWS[0].rendered() == "6 hours"


def test_fetch_falls_back_to_the_all_tier() -> None:
    # threshold_hours does not vary by tier, so a tier-2 lookup finds the ALL row.
    assert TABLES.fetch("travel", "2026.1", "tier-2", "travel_delay", "threshold_hours").value == "6"


def test_fetch_raises_rather_than_guessing() -> None:
    with pytest.raises(MissingRow):
        TABLES.fetch("travel", "2026.1", "tier-9", "travel_delay", "payout_per_block")


def test_transclusion_binds_every_figure_to_a_row() -> None:
    body = "Pays {{table:travel_delay.payout_per_block}} after {{table:travel_delay.threshold_hours}}."
    out = resolve_transclusions(body, TABLES, "travel", "2026.1", "tier-2")
    assert out.text == "Pays S$150 after 6 hours."
    assert [f.row_id for f in out.figures] == [
        "travel:2026.1:tier-2:travel_delay.payout_per_block",
        "travel:2026.1:ALL:travel_delay.threshold_hours",
    ]
    assert out.unresolved == []


def test_unresolvable_token_degrades_honestly() -> None:
    out = resolve_transclusions("Limit {{table:baggage_loss.limit}}.", TABLES, "travel", "2026.1", "tier-2")
    assert "[unavailable]" in out.text
    assert out.unresolved == ["baggage_loss.limit"]
    assert out.figures == []


def test_tier_specific_values_differ() -> None:
    two = TABLES.fetch("travel", "2026.1", "tier-2", "travel_delay", "payout_per_block").value
    three = TABLES.fetch("travel", "2026.1", "tier-3", "travel_delay", "payout_per_block").value
    assert two != three


def test_find_tokens() -> None:
    assert find_tokens("a {{table:x.y}} b {{table:p.q}}") == [("x", "y"), ("p", "q")]
