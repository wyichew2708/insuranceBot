"""Auto-evaluation pipeline CLI.

    evalgen generate   — derive FAQ pairs from the corpus, write the suite
    evalgen run        — run the suite against the bot and score it
    evalgen report     — render JSON + Markdown + HTML
    evalgen all        — the whole pipeline, with a pass-rate gate for CI

The suite is written to disk so a run is reproducible and reviewable: you can
read exactly which questions were asked and what evidence each expected.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

from api.settings import Settings

from evalgen.generator import generate
from evalgen.metrics import Report
from evalgen.report import write_all
from evalgen.runner import run_suite
from evalgen.schema import Suite
from okf import Bundle

DEFAULT_SUITE = Path(".eval-reports/auto-suite.json")
DEFAULT_OUT = Path(".eval-reports")


def _bundle(path: Path) -> Bundle:
    bundle = Bundle.load(path)
    if bundle.load_errors:
        print("bundle failed to load cleanly:", file=sys.stderr)
        for error in bundle.load_errors:
            print(f"  {error}", file=sys.stderr)
        raise SystemExit(2)
    return bundle


def cmd_generate(args: argparse.Namespace) -> int:
    bundle = _bundle(args.bundle)
    suite = generate(bundle, args.bundle, args.today)
    args.suite.parent.mkdir(parents=True, exist_ok=True)
    args.suite.write_text(suite.model_dump_json(indent=2))
    print(
        f"generated {suite.total} cases from {len(bundle.pages)} pages "
        f"and {len(bundle.tables)} table rows → {args.suite}"
    )
    for key, value in sorted(suite.stats.items()):
        if not key.startswith("category:"):
            print(f"  {key:14} {value}")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    bundle = _bundle(args.bundle)
    suite = (
        Suite.model_validate_json(args.suite.read_text())
        if args.suite.exists()
        else generate(bundle, args.bundle, args.today)
    )
    report = run_suite(bundle, Settings(bundle_path=args.bundle), suite)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "auto-eval.json").write_text(report.model_dump_json(indent=2))
    _print_summary(report)
    return 0 if report.accuracy >= args.gate else 1


def cmd_report(args: argparse.Namespace) -> int:
    report = Report.model_validate_json((args.out / "auto-eval.json").read_text())
    paths = write_all(report, args.out)
    for name, path in paths.items():
        print(f"{name:9} {path}")
    return 0


def cmd_all(args: argparse.Namespace) -> int:
    bundle = _bundle(args.bundle)
    suite = generate(bundle, args.bundle, args.today)
    args.suite.parent.mkdir(parents=True, exist_ok=True)
    args.suite.write_text(suite.model_dump_json(indent=2))
    print(f"generated {suite.total} cases from {len(bundle.pages)} pages and {len(bundle.tables)} table rows")

    report = run_suite(bundle, Settings(bundle_path=args.bundle), suite)
    paths = write_all(report, args.out)
    _print_summary(report)
    print()
    for name, path in paths.items():
        print(f"  {name:9} {path}")

    if report.accuracy < args.gate:
        print(f"\nFAIL — accuracy {report.accuracy:.1%} below gate {args.gate:.0%}", file=sys.stderr)
        return 1
    if report.unbound_figure_count:
        print(f"\nFAIL — {report.unbound_figure_count} unbound figures", file=sys.stderr)
        return 1
    if report.entitlement_leaks:
        print(f"\nFAIL — {report.entitlement_leaks} entitlement leaks", file=sys.stderr)
        return 1
    return 0


def _print_summary(report: Report) -> None:
    print()
    print(f"  accuracy            {report.accuracy:>7.1%}   ({report.total_cases} cases)")
    print(f"  citation F1         {report.citation_f1:>7.3f}")
    print(f"  figure exact match  {report.figure_exact_match:>7.1%}")
    print(
        f"  numeric binding     {report.numeric_binding_integrity:>7.1%}"
        f"   ({report.unbound_figure_count} unbound)"
    )
    print(f"  safety              {report.safety_score:>7.1%}   ({report.entitlement_leaks} leaks)")
    print(f"  merge consistency   {report.merge_passed:>3}/{report.merge_total}")
    print(f"  recall@1 / @3 / MRR {report.recall_at_1:.2f} / {report.recall_at_3:.2f} / {report.mrr:.2f}")
    print(f"  latency p50/p95     {report.latency_p50} / {report.latency_p95} ms")
    print(f"  corpus reach        {report.page_reach_rate:>7.1%}   (rows {report.row_coverage:.0%})")
    failures = [r for r in report.results if not r.passed]
    if failures:
        print(f"\n  {len(failures)} failing cases:")
        for r in failures[:12]:
            print(f"    {r.case_id:44} {'; '.join(r.failures)[:70]}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="evalgen", description=__doc__)
    parser.add_argument("--bundle", type=Path, default=Path("okf"))
    parser.add_argument("--suite", type=Path, default=DEFAULT_SUITE)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--gate", type=float, default=0.95)
    parser.add_argument("--today", type=dt.date.fromisoformat, default=dt.date.today())
    sub = parser.add_subparsers(dest="command", required=True)

    for name, fn, help_text in [
        ("generate", cmd_generate, "derive FAQ pairs from the corpus"),
        ("run", cmd_run, "run the suite and score it"),
        ("report", cmd_report, "render JSON + Markdown + HTML"),
        ("all", cmd_all, "generate, run, score and report"),
    ]:
        p = sub.add_parser(name, help=help_text)
        p.set_defaults(func=fn)

    args = parser.parse_args()
    raise SystemExit(args.func(args))


if __name__ == "__main__":
    main()
