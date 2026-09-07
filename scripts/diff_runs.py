"""Compare two conversation-evaluation runs, case by case.

A total tells you almost nothing. `1680/1711` is the same number whether the
change gained five cases, or gained forty-five and lost forty — and those are
not the same change. This prints the one thing the total hides: **what stopped
working.**

    make conversation-eval && cp .eval-reports/conversation.json /tmp/before.json
    # ...make the change...
    make conversation-eval && cp .eval-reports/conversation.json /tmp/after.json
    uv run python scripts/diff_runs.py /tmp/before.json /tmp/after.json --show-lost

Read the LOST list first. A change that gains forty and loses three is usually
not a good change: the three are a behaviour somebody relied on, and the forty
are often one pattern firing repeatedly. Every version from v2.4 to v2.8 was
held to zero lost, and in each of them the first cut lost something and was
narrowed until it did not — which is only possible if you look.

The groupings are the dataset's own labels rather than anything derived here,
so a regression shows up as a *shape* ("three renewal turns under the handoff
contract") rather than as three unrelated case ids.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from typing import Any

USAGE = "usage: diff_runs.py BEFORE.json AFTER.json [--show-lost]"

#: How the dataset labels a case. A loss is worth understanding as a group.
GROUP = ("section", "journey", "intent", "contract")


def _key(result: dict[str, Any]) -> tuple[str, ...]:
    return tuple(str(result.get(field, "")) for field in GROUP)


def _load(path: str) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    with open(path) as handle:
        report = json.load(handle)
    return {r["id"]: r for r in report["results"]}, report


def _counted(title: str, keys: list[tuple[str, ...]], limit: int = 30) -> None:
    print(f"\n{title}:")
    for key, n in Counter(keys).most_common(limit):
        print(f"  {n:3d} {key}")


def main(argv: list[str]) -> int:
    positional = [a for a in argv[1:] if not a.startswith("--")]
    if len(positional) != 2:
        print(USAGE, file=sys.stderr)
        return 2
    before_path, after_path = positional
    before, _ = _load(before_path)
    after, after_report = _load(after_path)

    passed_before = sum(1 for r in before.values() if r["passed"])
    print(f"A={before_path.split('/')[-1]} passed={passed_before}/{len(before)}")
    print(f"B={after_path.split('/')[-1]} passed={after_report['passed']}/{after_report['total']}")

    gained = [i for i, r in after.items() if r["passed"] and not before.get(i, {}).get("passed")]
    # A case absent from the baseline is not a loss — the suite grew.
    lost = [i for i, r in after.items() if not r["passed"] and before.get(i, {}).get("passed")]
    print(f"gained={len(gained)} lost={len(lost)}")

    _counted(f"LOST by {GROUP}", [_key(after[i]) for i in lost])
    print("\nLOST failure reasons:")
    for reason, n in Counter(f for i in lost for f in after[i]["failures"][:1]).most_common(20):
        print(f"  {n:3d} {reason[:110]}")
    _counted(f"GAINED by {GROUP}", [_key(after[i]) for i in gained])

    print("\nPass rate by section (B vs A):")
    for section in sorted({str(r.get("section", "")) for r in after.values()}):
        rows = [r for r in after.values() if str(r.get("section", "")) == section]
        baseline = [before[r["id"]] for r in rows if r["id"] in before]
        now = sum(1 for r in rows if r["passed"])
        was = sum(1 for r in baseline if r["passed"])
        print(f"  {section:22s} {now:4d}/{len(rows):<4d}  (was {was})")

    print("\nPass rate by intent (B vs A), sorted by delta:")
    deltas = []
    for intent in sorted({str(r.get("intent", "")) for r in after.values()}):
        rows = [r for r in after.values() if str(r.get("intent", "")) == intent]
        baseline = [before[r["id"]] for r in rows if r["id"] in before]
        now = sum(1 for r in rows if r["passed"])
        was = sum(1 for r in baseline if r["passed"])
        deltas.append((now - was, intent, now, len(rows), was))
    for delta, intent, now, total, was in sorted(deltas):
        print(f"  {delta:+4d} {intent:24s} {now:4d}/{total:<4d} (was {was})")

    if "--show-lost" in argv:
        # The turn that failed, not the last one: on a five-turn journey those
        # are rarely the same, and the first failure is the one to read.
        print("\nLOST detail:")
        for case_id in lost[:60]:
            result = after[case_id]
            turns = result.get("turn_results") or []
            turn = next((t for t in turns if not t["passed"]), turns[-1] if turns else {})
            print(
                f"- {case_id} [{result.get('intent')}/{result.get('contract')}]"
                f" say={turn.get('say', '')[:70]!r}"
                f"\n    fail={result['failures'][:2]}"
                f"\n    ans={turn.get('answer', '')[:140]!r}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
