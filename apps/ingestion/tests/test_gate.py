"""Eval-gated activation (§6.1.7 DoD): pass => activate, fail => stay staged."""

from contracts.api import PublishEvent
from contracts.settings import Settings
from ingestion.gate import handle_publish_event


def event() -> PublishEvent:
    return PublishEvent(bundle_id="cms-42", git_ref="main", delta=False)


def make_deps(pass_rate: float):  # type: ignore[no-untyped-def]
    calls: dict[str, object] = {"activated": None, "recorded": None}

    async def ingest() -> str:
        return "local-bundle-1"

    async def run_evals(bundle_id: str) -> float:
        assert bundle_id == "local-bundle-1"
        return pass_rate

    async def activate(bundle_id: str) -> None:
        calls["activated"] = bundle_id

    async def record(bundle_id: str, rate: float, activated: bool) -> None:
        calls["recorded"] = (bundle_id, rate, activated)

    return calls, ingest, run_evals, activate, record


async def test_passing_bundle_is_activated_and_recorded() -> None:
    calls, ingest, run_evals, activate, record = make_deps(pass_rate=1.0)
    result = await handle_publish_event(
        event(), Settings(eval_gate=0.95), ingest, run_evals, activate, record
    )
    assert result.activated
    assert calls["activated"] == "local-bundle-1"
    assert calls["recorded"] == ("local-bundle-1", 1.0, True)


async def test_failing_bundle_stays_staged() -> None:
    calls, ingest, run_evals, activate, record = make_deps(pass_rate=0.5)
    result = await handle_publish_event(
        event(), Settings(eval_gate=0.95), ingest, run_evals, activate, record
    )
    assert not result.activated
    assert calls["activated"] is None
    assert calls["recorded"] == ("local-bundle-1", 0.5, False)


async def test_gate_boundary_is_inclusive() -> None:
    _calls, ingest, run_evals, activate, record = make_deps(pass_rate=0.95)
    result = await handle_publish_event(
        event(), Settings(eval_gate=0.95), ingest, run_evals, activate, record
    )
    assert result.activated
