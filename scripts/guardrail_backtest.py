"""Backtest the guardrails: accuracy on labelled turns, generalisation on
held-out traffic, and how sensitive the decision is to where the bars sit.

Three questions, deliberately kept apart because they fail independently.

1. **Accuracy on the labelled corpus.** Precision and recall for the rule
   layer, per category. This corpus was used to *tune* the patterns, so a
   perfect score here says the patterns fit their training set and nothing
   more.

2. **Generalisation.** The same rules against benign traffic they were never
   shown: every question the generated eval suites ask, and — where a real
   crawl exists — every question the insurer publishes on its own websites.
   Neither was written with a guardrail in mind, so a false positive here is
   a real one. This is the number worth quoting.

3. **Sensitivity.** At what model confidence does each category flip, and how
   far is each threshold from the nearest boundary? A bar sitting a hundredth
   away from a decision it did not intend to make is a bug waiting for a model
   that phrases things slightly differently.

    uv run python scripts/guardrail_backtest.py [--out .eval-reports]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "apps" / "api"))

from api.guardrails import (  # noqa: E402
    INPUT_POLICY,
    OUTPUT_POLICY,
    Finding,
    Policy,
    Risk,
    Screening,
    screen_input_rules,
)

SCENARIOS = ROOT / "apps" / "api" / "tests" / "guardrail-scenarios.yaml"
SUITES = [ROOT / ".eval-reports" / "auto-suite.json", ROOT / ".eval-reports" / "web" / "auto-suite.json"]
CRAWL = ROOT / "okf-real" / "raw"

# A published question, from a heading or a bullet. Loose on purpose: the point
# is volume of real phrasing, and a few malformed fragments cost nothing
# because every one of them is still benign.
QUESTION_RE = re.compile(r"^#{1,6}\s*(.{8,140}\?)\s*$|^\s*[-*]\s*(.{8,140}\?)\s*$|^(.{12,140}\?)\s*$", re.M)


@dataclass
class Confusion:
    """Counted against `raised or not`, which is the decision that reaches a
    customer. Whether a raise was a flag or a block is scored separately."""

    tp: int = 0
    fp: int = 0
    tn: int = 0
    fn: int = 0
    fp_examples: list[str] = field(default_factory=list)
    fn_examples: list[str] = field(default_factory=list)

    @property
    def precision(self) -> float:
        return self.tp / (self.tp + self.fp) if self.tp + self.fp else 1.0

    @property
    def recall(self) -> float:
        return self.tp / (self.tp + self.fn) if self.tp + self.fn else 1.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if p + r else 0.0

    @property
    def total(self) -> int:
        return self.tp + self.fp + self.tn + self.fn

    def as_dict(self) -> dict[str, Any]:
        return {
            "tp": self.tp,
            "fp": self.fp,
            "tn": self.tn,
            "fn": self.fn,
            "precision": round(self.precision, 4),
            "recall": round(self.recall, 4),
            "f1": round(self.f1, 4),
            "false_positives": self.fp_examples[:10],
            "false_negatives": self.fn_examples[:10],
        }


def load_scenarios() -> list[tuple[str, dict[str, Any]]]:
    data = yaml.safe_load(SCENARIOS.read_text())
    return [(group, case) for group, cases in data.items() for case in cases]


def published_questions() -> list[str]:
    """Questions the insurer publishes, mined from the crawl.

    Genuinely held out: written by the business for its customers, years before
    anything here existed, and never consulted while tuning a pattern.
    """
    if not CRAWL.exists():
        return []
    seen: set[str] = set()
    for path in CRAWL.rglob("*.md"):
        try:
            text = path.read_text(errors="ignore")
        except OSError:
            continue
        for match in QUESTION_RE.finditer(text):
            raw = (match.group(1) or match.group(2) or match.group(3) or "").strip()
            raw = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", raw)
            raw = re.sub(r"[*_`#|]", "", raw).strip()
            if raw.endswith("?") and len(raw) >= 12 and raw.count("?") <= 2 and "http" not in raw:
                seen.add(raw)
    return sorted(seen)


def suite_questions() -> list[str]:
    out: list[str] = []
    for path in SUITES:
        if not path.exists():
            continue
        suite = json.loads(path.read_text())
        out += [c["question"] for c in suite["cases"]]
        out += [c["question"] for c in suite["merge_cases"]]
    return out


# --- 1. accuracy on the labelled corpus -----------------------------------


def score_labelled(cases: list[tuple[str, dict[str, Any]]]) -> tuple[Confusion, dict[str, Confusion]]:
    """The rule layer against every labelled turn.

    A turn the corpus marks `by: model` is a *negative* for the rules: they are
    not meant to catch it, and catching it would mean guessing at a semantic
    judgement — which is how the benign set got damaged the first time.
    """
    overall = Confusion()
    per_category: dict[str, Confusion] = {}
    for _group, case in cases:
        expected_raise = case["expect"] != "ok" and case["by"] == "rules"
        screening = screen_input_rules(case["q"])
        raised = bool(screening.findings)
        category = (
            screening.findings[0].category
            if screening.findings
            else ("(none)" if not expected_raise else case["expect"])
        )
        bucket = per_category.setdefault(category, Confusion())
        for target in (overall, bucket):
            if expected_raise and raised:
                target.tp += 1
            elif expected_raise and not raised:
                target.fn += 1
                target.fn_examples.append(case["q"])
            elif not expected_raise and raised:
                target.fp += 1
                target.fp_examples.append(f"{case['q']}  ->  {screening.summary()}")
            else:
                target.tn += 1
    return overall, per_category


# --- 2. generalisation ----------------------------------------------------


def score_held_out(name: str, questions: list[str]) -> dict[str, Any]:
    raised = [(q, screen_input_rules(q)) for q in questions]
    hits = [(q, s) for q, s in raised if s.findings]
    return {
        "corpus": name,
        "questions": len(questions),
        "raised": len(hits),
        "false_positive_rate": round(len(hits) / len(questions), 6) if questions else 0.0,
        "examples": [f"{q[:110]}  ->  {s.summary()}" for q, s in hits[:10]],
    }


# --- 3. sensitivity -------------------------------------------------------


def _lone_model(category: str, risk: Risk, confidence: float, side: str) -> Risk:
    finding = Finding(category, risk, "", "model", confidence)
    return Screening(findings=[finding], checked_by=["model"], side=side).risk


def flip_points(policies: dict[str, Policy], side: str) -> list[dict[str, Any]]:
    """The confidence at which a single model finding changes the outcome.

    Read this as the operating range. A category whose block point is above 1.0
    can never block on the model's word alone — sometimes intended, sometimes a
    weight and a bar that drifted apart until the check stopped working.
    """
    rows: list[dict[str, Any]] = []
    for category, policy in sorted(policies.items()):
        flag_at = None
        block_at = None
        for step in range(1, 101):
            confidence = step / 100
            if flag_at is None and _lone_model(category, Risk.flag, confidence, side) is not Risk.flag:
                continue
            if flag_at is None:
                flag_at = confidence
            if (
                block_at is None
                and policy.block_at is not None
                and _lone_model(category, Risk.block, confidence, side) is Risk.block
            ):
                block_at = confidence
        rows.append(
            {
                "side": side,
                "category": category,
                "rules_weight": policy.rules,
                "model_weight": policy.model,
                "flag_at": policy.flag_at,
                "block_at": policy.block_at,
                "model_alone_flags_from": flag_at,
                "model_alone_blocks_from": block_at,
                "rules_alone_blocks": policy.block_at is not None and policy.rules >= policy.block_at,
                # How much room a lone confident model has before the bar. A
                # thin margin means a model that hedges slightly stops acting.
                "block_margin_at_0_95": (
                    round(policy.model * 0.95 - policy.block_at, 3) if policy.block_at is not None else None
                ),
            }
        )
    return rows


def agreement_table() -> list[dict[str, Any]]:
    """What each combination of layer verdicts decides. The whole arbitration
    on one page, which is what an operator tuning it actually needs."""
    rows = []
    combos = [
        ("rules block, model silent", [Finding("injection", Risk.block, "", "rules", 1.0)]),
        ("model block @0.95, rules silent", [Finding("injection", Risk.block, "", "model", 0.95)]),
        ("model block @0.50, rules silent", [Finding("injection", Risk.block, "", "model", 0.50)]),
        (
            "both block",
            [
                Finding("injection", Risk.block, "", "rules", 1.0),
                Finding("injection", Risk.block, "", "model", 0.6),
            ],
        ),
        (
            "rules block, model reports nothing",
            [Finding("injection", Risk.block, "", "rules", 1.0)],
        ),
        ("model flag @0.90 on advice", [Finding("advice", Risk.flag, "", "model", 0.90)]),
        ("model block @1.00 on advice", [Finding("advice", Risk.block, "", "model", 1.00)]),
        ("six model flags on injection", [Finding("injection", Risk.flag, "", "model", 0.99)] * 6),
    ]
    for label, findings in combos:
        screening = Screening(findings=list(findings), checked_by=["rules"], side="input")
        rows.append(
            {"case": label, "verdict": screening.risk.value, "scores": [str(s) for s in screening.scores]}
        )
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=ROOT / ".eval-reports")
    parser.add_argument(
        "--max-false-positive-rate",
        type=float,
        default=0.0,
        help="fail if held-out benign traffic is raised above this rate",
    )
    args = parser.parse_args()

    cases = load_scenarios()
    overall, per_category = score_labelled(cases)

    held_out = [
        score_held_out("generated eval suites", suite_questions()),
        score_held_out("questions published on the websites", published_questions()),
    ]
    held_out = [h for h in held_out if h["questions"]]

    report: dict[str, Any] = {
        "labelled": {
            "cases": len(cases),
            "benign": sum(1 for _, c in cases if c["expect"] == "ok"),
            "hostile": sum(1 for _, c in cases if c["expect"] != "ok"),
            "rules_responsible": sum(1 for _, c in cases if c["by"] == "rules" and c["expect"] != "ok"),
            "model_responsible": sum(1 for _, c in cases if c["by"] == "model"),
            "overall": overall.as_dict(),
            "by_category": {k: v.as_dict() for k, v in sorted(per_category.items())},
        },
        "held_out": held_out,
        "sensitivity": flip_points(INPUT_POLICY, "input") + flip_points(OUTPUT_POLICY, "output"),
        "arbitration": agreement_table(),
    }

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "guardrail-backtest.json").write_text(json.dumps(report, indent=2) + "\n")

    print(
        f"labelled corpus      {len(cases)} turns "
        f"({report['labelled']['benign']} benign / {report['labelled']['hostile']} hostile)"
    )
    print(
        f"  rule layer         precision {overall.precision:.4f}  recall {overall.recall:.4f}  "
        f"F1 {overall.f1:.4f}"
    )
    print(f"                     tp {overall.tp}  fp {overall.fp}  tn {overall.tn}  fn {overall.fn}")
    if overall.fp_examples:
        print("  false positives:")
        for example in overall.fp_examples[:6]:
            print(f"    {example[:100]}")
    if overall.fn_examples:
        print("  false negatives:")
        for example in overall.fn_examples[:6]:
            print(f"    {example[:100]}")

    print("\nheld-out benign traffic (never used to tune a pattern)")
    worst = 0.0
    for entry in held_out:
        worst = max(worst, entry["false_positive_rate"])
        print(
            f"  {entry['corpus']:38} {entry['questions']:>6} questions  "
            f"{entry['raised']} raised  ({entry['false_positive_rate']:.4%})"
        )
        for example in entry["examples"][:5]:
            print(f"      {example[:110]}")

    print("\nwhere a lone model verdict starts to act")
    print(f"  {'side':7} {'category':18} {'flags from':>11} {'blocks from':>12} {'margin@0.95':>12}")
    for row in report["sensitivity"]:
        flags = f"{row['model_alone_flags_from']:.2f}" if row["model_alone_flags_from"] else "never"
        blocks = f"{row['model_alone_blocks_from']:.2f}" if row["model_alone_blocks_from"] else "never"
        margin = f"{row['block_margin_at_0_95']:+.3f}" if row["block_margin_at_0_95"] is not None else "-"
        print(f"  {row['side']:7} {row['category']:18} {flags:>11} {blocks:>12} {margin:>12}")

    print("\narbitration")
    for row in report["arbitration"]:
        print(f"  {row['verdict']:5}  {row['case']:36} {'; '.join(row['scores'])}")

    print(f"\n  → {args.out / 'guardrail-backtest.json'}")

    if overall.fp or overall.fn:
        print("\nFAIL — the labelled corpus does not pass cleanly", file=sys.stderr)
        return 1
    if worst > args.max_false_positive_rate:
        print(
            f"\nFAIL — held-out false-positive rate {worst:.4%} above {args.max_false_positive_rate:.4%}",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
