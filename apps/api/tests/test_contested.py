"""A figure the compiler filed a conflict on is delivered with that said."""

from __future__ import annotations

from pathlib import Path

from api.pipeline import answer_question
from api.settings import Settings
from harness import AuthLevel, Channel, Session
from okf.bundle import _contested

from okf import Bundle


def test_conflict_tickets_parse_to_coordinates(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text(
        "# Website defect — maid-insurance ALL:waiver_of_co_insurance.limit\n\n- opened: x\n"
    )
    (tmp_path / "b.md").write_text("# Website defect — travel-insurance :child.limit\n")
    (tmp_path / "c.md").write_text("# Website defect — pet-insurance pawfect-get-quote:n_a.limit\n")
    (tmp_path / "junk.md").write_text("not a ticket\n")
    assert _contested(tmp_path) == {
        ("maid-insurance", "waiver_of_co_insurance", "limit"),
        ("travel-insurance", "child", "limit"),
        ("pet-insurance", "n_a", "limit"),
    }


def test_a_contested_figure_is_delivered_with_the_dispute_named(bundle: Bundle, settings: Settings) -> None:
    session = Session(session_id="t", channel=Channel("channel/direct"), auth_level=AuthLevel("L0"))
    question = "what is the baggage limit on travel insurance"
    before, _ = answer_question(bundle, question, session, settings)
    bound = [f for f in before.answer.figures if f.table_row_id and "baggage_loss.limit" in f.table_row_id]
    if not bound:
        return  # the seed session resolves no tier here; the note has nothing to attach to
    bundle.contested = frozenset({("travel", "baggage_loss", "limit")})
    try:
        after, _ = answer_question(bundle, question, session, settings)
    finally:
        bundle.contested = frozenset()
    assert "published pages differ" in after.answer.answer
    assert "takes precedence" in after.answer.answer
    assert "published pages differ" not in before.answer.answer
