"""Score the golden conversation dataset, grouped by the taxonomy's own layers.

    uv run python scripts/conversation_report.py --bundle okf-real
    make conversation-eval

One pass rate over 1,356 cases is not a performance measurement — it is a
number that goes up and down for reasons you cannot see. The dataset carries
three labels on every case (journey, intent, entities, plus the behaviour
contract and the product), and this reports the score by each of them, so the
output reads as "claims questions on motor products are weak" and "the bot
answers status enquiries it should be handing off".

The second of those is the reason `contract` is a grouping. A failure against
the `handoff` contract means the bot answered something no corpus can answer,
which is a different and more serious defect than failing to find a clause —
and averaged into one number the two are indistinguishable.

Writes `.eval-reports/conversation.{json,md}`.
"""

from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / p) for p in ("packages/okf", "packages/harness", "apps/api", ".")]


def _rate(results: list[dict[str, Any]]) -> float:
    return sum(1 for r in results if r["passed"]) / len(results) if results else 0.0


def _group(results: list[dict[str, Any]], key: str) -> list[tuple[str, int, int, float]]:
    """(label, passed, total, rate), worst first, ties broken by size."""
    buckets: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    for result in results:
        value = result.get(key)
        for label in value if isinstance(value, list) else [value]:
            if label:
                buckets[str(label)].append(result)
    rows = [(name, sum(1 for r in rs if r["passed"]), len(rs), _rate(rs)) for name, rs in buckets.items()]
    return sorted(rows, key=lambda row: (row[3], -row[2], row[0]))


def _table(title: str, rows: list[tuple[str, int, int, float]], limit: int | None = None) -> list[str]:
    out = [f"\n### {title}\n", "| | pass | of | rate |", "|---|---:|---:|---:|"]
    for name, passed, total, rate in rows[:limit] if limit else rows:
        out.append(f"| {name} | {passed} | {total} | {rate:.0%} |")
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, default=ROOT / "okf-real")
    parser.add_argument("--suite", default="conversation")
    parser.add_argument("--out", type=Path, default=ROOT / ".eval-reports")
    parser.add_argument("--gate", type=float, default=0.0, help="minimum overall pass rate")
    args = parser.parse_args()

    from api.settings import Settings

    from evals.runner import run_suite_file
    from okf import Bundle

    settings = Settings(bundle_path=args.bundle)
    suite_path = ROOT / "evals" / "suites" / f"{args.suite}.yaml"
    report = run_suite_file(Bundle.load(args.bundle), settings, suite_path)
    results = report["results"]

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "conversation.json").write_text(json.dumps(report, indent=2, default=str))

    lines = [
        f"# Conversation golden dataset — {report['passed']}/{report['total']} ({report['pass_rate']:.1%})",
        "",
        f"Bundle `{args.bundle.name}`, deterministic composer, "
        f"{len({r.get('product') for r in results if r.get('product')})} products.",
    ]
    # Conversations are scored twice over, and the two numbers answer different
    # questions. Turn accuracy is how often the bot is right. Conversation
    # accuracy is how often a customer got all the way through a journey
    # without a bad answer — and on a five-turn journey the second is the one
    # they experience.
    convos = [r for r in results if r.get("archetype")]
    turns = [t for r in results for t in (r.get("turn_results") or []) if t.get("checked")]
    if convos:
        contextual = [t for t in turns if t.get("needs_context")]
        standalone = [t for t in turns if not t.get("needs_context")]
        lines += [
            f"\n## Conversations — {len(convos)} journeys, {len(turns)} scored turns\n",
            "| | pass | of | rate |",
            "|---|---:|---:|---:|",
            f"| **whole conversations** (every turn right) | {sum(1 for c in convos if c['passed'])} "
            f"| {len(convos)} | {_rate(convos):.0%} |",
            f"| turns overall | {sum(1 for t in turns if t['passed'])} | {len(turns)} | {_rate(turns):.0%} |",
            f"| — standalone turns | {sum(1 for t in standalone if t['passed'])} | {len(standalone)} "
            f"| {_rate(standalone):.0%} |",
            f"| — context-dependent turns | {sum(1 for t in contextual if t['passed'])} "
            f"| {len(contextual)} | {_rate(contextual):.0%} |",
        ]
        lines += _table("Turns by what they do to the conversation", _group(turns, "kind"))
        lines += _table("Turns by contract", _group(turns, "contract"))
        lines += _table("Conversations by archetype", _group(convos, "archetype"))

    lines += _table("By behaviour contract — what kind of reply was owed", _group(results, "contract"))
    lines += _table("By journey — where in the lifecycle", _group(results, "journey"))
    lines += _table("By intent — weakest 25", _group(results, "intent"), limit=25)
    lines += _table("By section — the taxonomy's own chapters", _group(results, "section"))
    lines += _table("By product — weakest 20", _group(results, "product"), limit=20)
    lines += _table("By brand", _group(results, "brand"))
    lines += _table("By entity label — weakest 25", _group(results, "entities"), limit=25)

    # Severity, for the contracts where a miss can be dangerous. A case that
    # owed a handoff and got one of these instead is not one failure mode but
    # three, and they need different fixes: a substantive answer to "is this
    # email really from you" is a safety defect; a clarifying question is a
    # wasted turn; a refusal the gates produced is very nearly right.
    owed_handoff = [
        r for r in results if r.get("contract") in {"handoff", "out_of_scope"} and not r["passed"]
    ]
    if owed_handoff:
        severity: collections.Counter[str] = collections.Counter()
        for result in owed_handoff:
            seen = result.get("observed") or {}
            if seen.get("clarifying"):
                severity["asked which product instead of handing off"] += 1
            elif not seen.get("delivered"):
                severity["blocked by a gate rather than handed off"] += 1
            elif seen.get("smalltalk"):
                severity["treated as smalltalk"] += 1
            else:
                severity["ANSWERED — a substantive reply it could not support"] += 1
        lines += [
            f"\n### Owed a handoff and did not give one ({len(owed_handoff)} cases)\n",
            "| what it did instead | count |",
            "|---|---:|",
        ]
        for mode, count in severity.most_common():
            lines.append(f"| {mode} | {count} |")

    # The failure modes themselves, counted. A pass rate says how much is
    # broken; this says what breaks.
    modes: collections.Counter[str] = collections.Counter()
    for result in results:
        for failure in result["failures"]:
            head = failure.split(",")[0].split(":")[0].strip()
            modes[head[:70]] += 1
    lines += ["\n### Failure modes\n", "| | count |", "|---|---:|"]
    for mode, count in modes.most_common(20):
        lines.append(f"| {mode} | {count} |")

    (args.out / "conversation.md").write_text("\n".join(lines) + "\n")

    print("\n".join(lines[:4]))
    if convos:
        print(f"\n  conversations   {len(convos)} journeys, {len(turns)} scored turns")
        print(
            f"    whole           {sum(1 for c in convos if c['passed']):5d}/{len(convos):<5d} "
            f"{_rate(convos):6.1%}   (every turn right)"
        )
        print(
            f"    turns           {sum(1 for t in turns if t['passed']):5d}/{len(turns):<5d} "
            f"{_rate(turns):6.1%}"
        )
        contextual = [t for t in turns if t.get("needs_context")]
        print(
            f"    needs context   {sum(1 for t in contextual if t['passed']):5d}/{len(contextual):<5d} "
            f"{_rate(contextual):6.1%}   (unanswerable without the turns before)"
        )
        print("\n  turns by kind:")
        for name, passed, total, rate in _group(turns, "kind"):
            print(f"    {name:14s} {passed:5d}/{total:<5d} {rate:6.1%}")
    for title, key in (("contract", "contract"), ("journey", "journey")):
        print(f"\n  by {title}:")
        for name, passed, total, rate in _group(results, key):
            print(f"    {name:22s} {passed:5d}/{total:<5d} {rate:6.1%}")
    if owed_handoff:
        print(f"\n  owed a handoff and did not give one — {len(owed_handoff)}:")
        for mode, count in severity.most_common():
            print(f"    {count:5d}  {mode}")
    print(f"\n  report  {(args.out / 'conversation.md').relative_to(ROOT)}")
    if report["pass_rate"] < args.gate:
        print(f"\nFAIL: {report['pass_rate']:.1%} below gate {args.gate:.0%}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
