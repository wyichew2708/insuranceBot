"""Run the generated suite in batches, on the record and resumable.

The real corpus generates ~26,000 cases and takes around two hours. Run as one
call that is a bad deal: nothing is visible until it finishes, a machine that
sleeps loses the lot, and the only way to compare two runs is to remember what
the last one printed. Three times this session a long job was killed or hung
and had to start again from zero.

So: fixed-size batches, each written to disk the moment it completes, each
printing the running accuracy. A rerun skips what is already there. The final
score is computed from the batch files rather than from memory, which means it
can be recomputed later — or after a crash — without running anything.

    uv run python scripts/eval_batches.py --bundle okf-real --batch-size 500
    uv run python scripts/eval_batches.py --bundle okf-real --report-only

Deterministic by default and pinned to it. A configured model would make this
three API calls per case — around 78,000 for one pass over the real corpus —
which is not a thing to start by accident.
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import datetime as dt
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for package in ("apps/api", "apps/evalgen", "packages/okf", "packages/harness"):
    sys.path.insert(0, str(ROOT / package))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, default=Path("okf-real"))
    parser.add_argument("--suite", type=Path, default=Path(".eval-reports/auto-suite.json"))
    parser.add_argument("--out", type=Path, default=Path(".eval-reports/batches"))
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument(
        "--limit", type=int, default=0, help="stop after this many cases (0 = all); for a smoke run"
    )
    parser.add_argument(
        "--report-only",
        action="store_true",
        help="score the batches already on disk and stop — no cases are run",
    )
    parser.add_argument("--live", action="store_true", help="use whatever .env configures, and pay for it")
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help=(
            "cases to run at once. A local MLX server batches concurrent requests, so this is "
            "throughput rather than parallelism: measured 0.57 req/s at one worker and 0.93 at "
            "twelve. Serial is the right default — it keeps the run reproducible and leaves the "
            "machine usable — but the full real-corpus suite is 24,732 cases, and at one worker "
            "that is days rather than hours."
        ),
    )
    args = parser.parse_args()

    if not args.live:
        # Pinned rather than defaulted: `Settings` reads `.env`, and a key
        # sitting there turns a reporting run into tens of thousands of billed
        # requests without a word about it.
        os.environ["LLM_PROVIDER"] = "deterministic"
        os.environ["GUARDRAILS"] = "rules"

    from api.settings import Settings
    from api.sor import register_bundle_policies
    from evalgen.metrics import CaseResult, Coverage, score
    from evalgen.runner import run_case
    from evalgen.schema import Suite

    from okf import Bundle

    args.out.mkdir(parents=True, exist_ok=True)

    if not args.suite.is_file():
        print(f"no suite at {args.suite} — run `evalgen generate` first", file=sys.stderr)
        return 1
    suite = Suite.model_validate_json(args.suite.read_text())
    cases = suite.cases[: args.limit] if args.limit else suite.cases
    batches = [cases[i : i + args.batch_size] for i in range(0, len(cases), args.batch_size)]

    print(f"suite {suite.name} · bundle {suite.bundle} · {len(cases)} cases · {len(batches)} batches")

    if not args.report_only:
        settings = Settings(bundle_path=args.bundle)
        bundle = Bundle.load(args.bundle)
        # `run_suite` does this and this script calls `run_case` directly. The
        # generated figure cases carry a policy id per benefit-table row, and
        # without the registration every one of them raises on an unknown
        # fixture policy.
        register_bundle_policies(bundle)
        started = time.perf_counter()
        done_cases = 0
        # Which engine actually answered. A local model server that dies mid-run
        # takes every later case with it *silently*: `provider_for` degrades to
        # the deterministic composer per case, the run gets ~70x faster, and the
        # score it prints is a real number for a system nobody is shipping. That
        # happened here — a 3.5-day run turned into a 4-minute one and the only
        # tell was the clock.
        composers: Counter[str] = Counter()

        for number, batch in enumerate(batches):
            path = args.out / f"batch-{number:04d}.json"
            if path.is_file():
                print(f"  batch {number:3}/{len(batches)}  skipped (already on disk)")
                done_cases += len(batch)
                continue
            batch_started = time.perf_counter()
            if args.workers > 1:
                # Order is restored below: a batch file that depended on thread
                # scheduling would make two runs of the same suite diff against
                # each other for no reason.
                with cf.ThreadPoolExecutor(max_workers=args.workers) as pool:
                    pairs = list(pool.map(lambda c: run_case(bundle, settings, c), batch))
            else:
                pairs = [run_case(bundle, settings, case) for case in batch]
            results = [r for r, _ in pairs]
            composers.update(t.composer or "unknown" for _, t in pairs)
            # Written before the next batch starts, so a kill costs one batch.
            path.write_text(
                json.dumps(
                    {
                        "batch": number,
                        "bundle": str(args.bundle),
                        "ran_at": dt.datetime.now().isoformat(timespec="seconds"),
                        "results": [r.model_dump(mode="json") for r in results],
                    }
                )
            )
            done_cases += len(batch)
            passed = sum(1 for r in results if r.passed)
            elapsed = time.perf_counter() - started
            rate = done_cases / elapsed if elapsed else 0
            left = (len(cases) - done_cases) / rate if rate else 0
            print(
                f"  batch {number:3}/{len(batches)}  {passed:4}/{len(batch):<4} "
                f"({passed / len(batch):6.1%})  "
                f"{time.perf_counter() - batch_started:5.1f}s  "
                f"cumulative {done_cases}/{len(cases)}  eta {left / 60:.0f}m",
                flush=True,
            )
            if not args.live:
                continue
            served = sum(n for engine, n in composers.items() if not engine.startswith("deterministic"))
            if done_cases >= 100 and served / done_cases < 0.5:
                print(
                    f"\nSTOPPED: {done_cases - served} of {done_cases} cases were answered by the "
                    f"deterministic composer, not the configured model.\n"
                    f"  composers so far: {dict(composers)}\n"
                    f"  The model is probably down. Scoring this would report a number for a "
                    f"system nobody is running.",
                    file=sys.stderr,
                )
                return 1

    # Scored from the files, never from memory: the same command reproduces the
    # number after a crash, on another machine, or a week later.
    results = []
    for path in sorted(args.out.glob("batch-*.json")):
        payload = json.loads(path.read_text())
        results.extend(CaseResult.model_validate(r) for r in payload["results"])
    if not results:
        print("no batches on disk", file=sys.stderr)
        return 1

    bundle = Bundle.load(args.bundle)
    coverage = Coverage(total_pages=len(bundle.pages), total_rows=len(bundle.tables))
    for result in results:
        coverage.pages_cited.update(result.cited)
        coverage.pages_loaded.update(result.loaded_pages)
        coverage.rows_exercised.update(result.figure_row_ids)

    report = score(
        results,
        [],
        coverage,
        suite=suite.name,
        bundle=suite.bundle,
        generated_at=suite.generated_at,
        ran_at=dt.datetime.now().isoformat(timespec="seconds"),
        wall_clock_s=0.0,
    )
    summary = args.out / "summary.json"
    summary.write_text(report.model_dump_json(indent=1))

    r = report
    print()
    print(f"  cases scored          {len(results)}")
    print(f"  accuracy              {r.accuracy:.1%}")
    print(f"  citation F1           {r.citation_f1:.3f}")
    print(f"  figure exact match    {r.figure_exact_match:.1%}")
    print(f"  numeric binding       {r.numeric_binding_integrity:.1%}   ({r.unbound_figure_count} unbound)")
    print(f"  entitlement leaks     {r.entitlement_leaks}")
    print(f"  failure shape         {r.unsafe_failures} unsafe / {r.miss_failures} safe misses")
    print(f"  wrong product         {r.wrong_product}   (delivered, cited another product)")
    print(f"  recall@1 / @3 / MRR   {r.recall_at_1:.2f} / {r.recall_at_3:.2f} / {r.mrr:.2f}")
    print(f"  corpus reach          {r.page_reach_rate:.1%}   (rows {r.row_coverage:.1%})")
    print(f"\n  {summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
