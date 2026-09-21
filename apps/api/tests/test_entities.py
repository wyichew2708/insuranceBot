"""Self-reported inputs remain bounded, explicit and isolated across contexts."""

import datetime as dt
from decimal import Decimal
from pathlib import Path

from api.entities import EntitySlots, extract_slots, restore_slots
from api.memory import SessionMemory
from api.pipeline import answer_question
from api.settings import Settings
from harness import Session

from okf import Bundle


def test_extract_and_merge_explicit_inputs() -> None:
    first = extract_slots("I'm 42, travelling to Japan for 7 days, tier: deluxe", tiers=("classic", "deluxe"))
    assert first.age == 42
    assert first.destination == "JP"
    assert first.tier == "deluxe"
    assert first.duration_days == 7
    followup = extract_slots("and for twelve days?", first, tiers=("classic", "deluxe"))
    assert followup.duration_days == 12
    assert followup.destination == "JP"
    assert followup.age == 42
    insured = extract_slots("sum insured: SGD 100,000.50; occupation: teacher; vehicle: private car")
    assert insured.sum_insured == Decimal("100000.50")
    assert insured.currency == "SGD"
    assert insured.occupation == "teacher"
    assert insured.vehicle == "private car"


def test_bad_or_ambiguous_updates_clear_previous_values() -> None:
    previous = EntitySlots(age=40, destination="JP", duration_days=7, tier="classic")
    slots = extract_slots("I'm 999, for 900 days, destination: Atlantis, tier: imaginary", previous)
    assert slots.age is None
    assert slots.destination is None
    assert slots.duration_days is None
    assert slots.tier is None
    assert extract_slots("to Japan or Malaysia", previous).destination is None
    assert extract_slots("classic or deluxe", previous, tiers=("classic", "deluxe")).tier is None
    assert extract_slots("I'm 30, I'm 40").age is None
    assert extract_slots("Does this cover people aged 65?").age is None
    assert (
        extract_slots("Policy 42 costs $100 for coverage up to 65 years").model_dump(exclude_none=True) == {}
    )
    assert extract_slots("age: unknown", previous).age is None
    assert extract_slots("I'm -1", previous).age is None
    assert extract_slots("duration: -2 days", previous).duration_days is None
    assert extract_slots("what if I'm 40?").age is None
    assert extract_slots("not going to Japan", previous).destination is None
    assert extract_slots("going to Atlantis", previous).destination is None
    assert extract_slots("sum insured: ,,,").sum_insured is None
    assert extract_slots("sum insured: 1,2").sum_insured is None
    assert extract_slots("sum insured: 100 and sum insured: 200").sum_insured is None
    assert restore_slots({"age": -1}) == EntitySlots()
    assert restore_slots({"destination": "Atlantis"}) == EntitySlots()


def test_iso_dates_and_conflicts_are_not_guessed() -> None:
    slots = extract_slots("from 2026-10-01 until 2026-10-12")
    assert slots.trip_start == dt.date(2026, 10, 1)
    assert slots.trip_end == dt.date(2026, 10, 12)
    assert slots.duration_days == 12
    assert extract_slots("from 01/10/2026 until 12/10/2026") == EntitySlots()
    assert extract_slots("from 2026-02-30").trip_start is None
    reversed_dates = extract_slots("from 2026-10-12 until 2026-10-01")
    assert reversed_dates.trip_end is None
    assert reversed_dates.duration_days is None
    conflict = extract_slots("from 2026-10-01 until 2026-10-12 for 7 days")
    assert conflict.duration_days is None


def test_slots_survive_restart_and_reset_with_history_or_product(tmp_path: Path) -> None:
    bundle = Bundle.load(Path("okf-real"))
    settings = Settings(bundle_path=bundle.root, memory="on", state_dir=tmp_path)
    session = Session(session_id="entity-inputs", today=dt.date(2026, 9, 4))
    answer_question(bundle, "Travel Infinite quote, I'm 42, to Japan for 7 days", session, settings)
    _, trace = answer_question(bundle, "and for twelve days?", session, settings)
    assert trace.slots["destination"] == "JP"
    assert trace.slots["duration_days"] == 12
    recalled = SessionMemory(tmp_path).recall(session.session_id)
    assert recalled.state.slots.duration_days == 12
    _, fresh = answer_question(bundle, "for 3 days", session, settings, history=[])
    assert fresh.slots == {"duration_days": 3}
    _, switched = answer_question(bundle, "Tiq Home Insurance quote", session, settings)
    assert switched.slots == {}
    _, supplied = answer_question(
        bundle,
        "and for twelve days?",
        session,
        settings,
        history=["Travel Infinite to Japan for 7 days"],
    )
    assert supplied.slots["destination"] == "JP"
    assert supplied.slots["duration_days"] == 12
