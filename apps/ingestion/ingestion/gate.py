"""Publish-event handling with eval-gated activation (§6.1.7).

Flow per event: sync repo -> ingest staged (inactive) -> run the eval suite
against the running stack -> activate only if pass-rate >= EVAL_GATE, else
leave staged and record the failure. Dependencies are injected callables so
the gating logic is unit-testable without a stack.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from contracts.api import PublishEvent
from contracts.settings import Settings

logger = logging.getLogger("ingestion.gate")

IngestFn = Callable[[], Awaitable[str]]  # returns bundle_id (staged inactive)
EvalFn = Callable[[str], Awaitable[float]]  # bundle_id -> pass rate
ActivateFn = Callable[[str], Awaitable[None]]
RecordFn = Callable[[str, float, bool], Awaitable[None]]  # bundle_id, pass_rate, activated


@dataclass
class GateResult:
    bundle_id: str
    pass_rate: float
    activated: bool


async def handle_publish_event(
    event: PublishEvent,
    settings: Settings,
    ingest: IngestFn,
    run_evals: EvalFn,
    activate: ActivateFn,
    record: RecordFn | None = None,
) -> GateResult:
    bundle_id = await ingest()
    logger.info("bundle %s staged for publish event %s (delta=%s)", bundle_id, event.bundle_id, event.delta)

    pass_rate = await run_evals(bundle_id)
    activated = pass_rate >= settings.eval_gate
    if activated:
        await activate(bundle_id)
        logger.info("bundle %s activated (pass rate %.1f%%)", bundle_id, pass_rate * 100)
    else:
        logger.error(
            "bundle %s NOT activated: pass rate %.1f%% below gate %.1f%%",
            bundle_id,
            pass_rate * 100,
            settings.eval_gate * 100,
        )
    if record is not None:
        await record(bundle_id, pass_rate, activated)
    return GateResult(bundle_id=bundle_id, pass_rate=pass_rate, activated=activated)


async def run_eval_suite_subprocess(
    suite_path: str = "evals/golden/smoke.yaml",
    runner_path: str = "evals/runner.py",
    gateway_url: str = "http://localhost:8000",
) -> float:
    """Run the eval suite via the runner and return the pass rate. Missing
    runner/suite returns 0.0 — the safe default is to never activate an
    unverified bundle."""
    import asyncio
    import sys
    from pathlib import Path

    if not Path(runner_path).exists() or not Path(suite_path).exists():
        logger.error("eval runner or suite missing (%s, %s); refusing pass", runner_path, suite_path)
        return 0.0
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        runner_path,
        "--suite",
        suite_path,
        "--gateway-url",
        gateway_url,
        "--gate",
        "0",  # gating decision is ours; runner just reports
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    output, _ = await proc.communicate()
    report_path = Path(".eval-reports") / f"{Path(suite_path).stem}.json"
    try:
        report = json.loads(report_path.read_text())
        results = report["results"]
        return sum(1 for r in results if r["passed"]) / len(results) if results else 0.0
    except Exception as exc:
        logger.error("could not read eval report (%s); runner output:\n%s", exc, output.decode()[-2000:])
        return 0.0


async def record_eval_run(
    settings: Settings, bundle_id: str, pass_rate: float, activated: bool, suite: str = "publish-gate"
) -> None:
    if not settings.database_url:
        return
    try:
        import psycopg

        dsn = settings.database_url.replace("postgresql+psycopg://", "postgresql://")
        async with await psycopg.AsyncConnection.connect(dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "INSERT INTO eval_runs (bundle_id, suite, pass_rate, report) VALUES (%s, %s, %s, %s)",
                    (bundle_id, suite, pass_rate, json.dumps({"activated": activated})),
                )
            await conn.commit()
    except Exception as exc:
        logger.warning("could not record eval run: %s", exc)
