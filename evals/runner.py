"""Eval harness (§5.4): loads a suite yaml, calls the chat API, applies
checks, writes eval_runs (when a DB is configured) plus a JSON report, prints
a table, exits non-zero if pass-rate < EVAL_GATE.

Usage: python evals/runner.py --suite evals/golden/smoke.yaml [--gateway-url http://...]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import yaml


@dataclass
class CaseResult:
    case_id: str
    passed: bool
    failures: list[str] = field(default_factory=list)


def load_suite(path: Path) -> list[dict[str, Any]]:
    cases = yaml.safe_load(path.read_text())
    if not isinstance(cases, list):
        raise ValueError(f"suite {path} must be a list of cases")
    return cases


async def run_case(case: dict[str, Any], gateway_url: str, http: httpx.AsyncClient) -> CaseResult:
    result = CaseResult(case_id=str(case["id"]), passed=True)
    expect = case.get("expect", {}) or {}
    try:
        resp = await http.post(
            f"{gateway_url}/v1/chat",
            json={
                "session_id": f"eval-{case['id']}",
                "brand": case.get("brand", "tiq"),
                "audience": case.get("audience", "public"),
                "message": case["question"],
            },
            timeout=60,
        )
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        result.passed = False
        result.failures.append(f"chat call failed: {exc}")
        return result

    text_parts: list[str] = []
    citations: list[str] = []
    actions: list[str] = []
    route: str | None = None
    for line in resp.text.splitlines():
        line = line.strip()
        if not line.startswith("data: "):
            continue
        event = json.loads(line[len("data: ") :])
        if event.get("type") == "token" and event.get("text"):
            text_parts.append(event["text"])
        elif event.get("type") == "citation" and event.get("chunk_id"):
            citations.append(event["chunk_id"])
        elif event.get("type") == "action" and event.get("action_id"):
            actions.append(event["action_id"])
        elif event.get("type") == "done":
            route = event.get("route")
    answer = " ".join(text_parts)

    def fail(msg: str) -> None:
        result.passed = False
        result.failures.append(msg)

    wanted_route = expect.get("route")
    if wanted_route and route != wanted_route:
        fail(f"route={route!r}, expected {wanted_route!r}")
    for block_id in expect.get("must_cite", []) or []:
        if not any(c == block_id or c.startswith(f"{block_id}#") for c in citations):
            fail(f"missing citation {block_id!r} (got {citations})")
    for needle in expect.get("must_contain", []) or []:
        if needle.lower() not in answer.lower():
            fail(f"answer missing {needle!r}")
    for needle in expect.get("must_not_contain", []) or []:
        if needle.lower() in answer.lower():
            fail(f"answer must not contain {needle!r}")
    for token in expect.get("verbatim", []) or []:
        if token not in answer and token not in " ".join(actions):
            fail(f"verbatim token {token!r} absent")
    action_id = expect.get("action")
    if action_id and action_id not in actions:
        fail(f"expected action {action_id!r} (got {actions})")
    return result


def write_eval_run(suite: str, pass_rate: float, report: dict[str, Any], database_url: str) -> None:
    try:
        import psycopg

        dsn = database_url.replace("postgresql+psycopg://", "postgresql://")
        with psycopg.connect(dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO eval_runs (bundle_id, git_sha, suite, pass_rate, report)"
                " VALUES (%s, %s, %s, %s, %s)",
                (
                    os.environ.get("BUNDLE_ID"),
                    os.environ.get("GIT_SHA"),
                    suite,
                    pass_rate,
                    json.dumps(report),
                ),
            )
    except Exception as exc:  # eval result recording must not mask the gate itself
        print(f"warning: could not write eval_runs: {exc}", file=sys.stderr)


async def run_suite(suite_path: Path, gateway_url: str) -> tuple[float, list[CaseResult]]:
    cases = load_suite(suite_path)
    async with httpx.AsyncClient() as http:
        results = [await run_case(case, gateway_url, http) for case in cases]
    passed = sum(1 for r in results if r.passed)
    return (passed / len(results) if results else 0.0), results


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--suite", type=Path, default=Path("evals/golden/smoke.yaml"))
    parser.add_argument("--gateway-url", default=os.environ.get("GATEWAY_URL", "http://localhost:8000"))
    parser.add_argument("--gate", type=float, default=float(os.environ.get("EVAL_GATE", "0.95")))
    args = parser.parse_args()

    pass_rate, results = asyncio.run(run_suite(args.suite, args.gateway_url))

    width = max((len(r.case_id) for r in results), default=10)
    print(f"\n{'case'.ljust(width)}  result")
    print("-" * (width + 10))
    for r in results:
        print(f"{r.case_id.ljust(width)}  {'PASS' if r.passed else 'FAIL'}")
        for failure in r.failures:
            print(f"{' ' * width}    - {failure}")
    print(f"\npass rate: {pass_rate:.1%} (gate {args.gate:.0%})")

    report = {"results": [{"id": r.case_id, "passed": r.passed, "failures": r.failures} for r in results]}
    report_dir = Path(".eval-reports")
    report_dir.mkdir(exist_ok=True)
    (report_dir / f"{args.suite.stem}.json").write_text(json.dumps(report, indent=2))

    if database_url := os.environ.get("DATABASE_URL", ""):
        write_eval_run(args.suite.stem, pass_rate, report, database_url)

    if pass_rate < args.gate:
        sys.exit(1)


if __name__ == "__main__":
    main()
