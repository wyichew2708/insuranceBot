"""Calibration: the two ways a guardrail fails, measured against labelled turns.

A missed attack is a breach. A blocked customer is a person who arrived with a
claim and was told "I can't help with that" — and they do not try a second
phrasing, they leave. Both directions are asserted here, and the benign side is
weighted heavier because insurance language is full of the words a naive filter
reaches for: death, cancellation, rules, instructions, override, "act as".

`guardrail-scenarios.yaml` carries the labels. `by: rules` means the
deterministic layer must reach the verdict unaided; `by: model` means it needs a
reader, and the rules must *not* guess — a regex approximating a semantic
judgement is precisely how the benign set gets damaged.
"""

from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml
from api.guardrails import Risk, screen_input_rules
from api.pipeline import answer_question
from api.settings import Settings
from harness import AuthLevel, Channel, Session

from okf import Bundle

SCENARIOS = Path(__file__).parent / "guardrail-scenarios.yaml"

# The backtest harness is a script rather than a package; importing it here
# keeps one implementation of the accuracy maths instead of a second copy that
# drifts.
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
from guardrail_backtest import flip_points, load_scenarios, score_labelled  # noqa: E402


def _load() -> list[tuple[str, dict[str, Any]]]:
    data = yaml.safe_load(SCENARIOS.read_text())
    return [(group, case) for group, cases in data.items() for case in cases]


CASES = _load()
BENIGN = [(g, c) for g, c in CASES if c["expect"] == "ok"]
BY_RULES = [(g, c) for g, c in CASES if c["by"] == "rules" and c["expect"] != "ok"]
BY_MODEL = [(g, c) for g, c in CASES if c["by"] == "model"]


def test_the_corpus_covers_both_directions() -> None:
    """This file is attack-dense on purpose, and that is a change from how it
    started.

    When the benign side had to carry the false-positive measurement itself it
    was the larger half. It no longer has to: the held-out corpora below are
    thousands of benign questions the patterns were never shown, and they are a
    far better estimate than any list written by the person tuning the
    patterns. So the labelled set is free to spend its weight on attack
    families, where breadth cannot come from anywhere else — while still
    keeping enough benign cases to pin the specific traps that broke earlier
    versions.
    """
    assert len(CASES) >= 180
    assert len(BENIGN) >= 80
    assert BY_RULES and BY_MODEL


@pytest.mark.parametrize(("group", "case"), BENIGN, ids=[c["q"][:48] for _, c in BENIGN])
def test_no_ordinary_customer_turn_is_raised(group: str, case: dict[str, Any]) -> None:
    """The hard requirement. Nothing a real customer would type may be blocked
    or flagged by the rule layer, whatever words it happens to contain."""
    screening = screen_input_rules(case["q"])
    assert screening.risk is Risk.ok, f"[{group}] raised {screening.summary()}: {case['q']}"


@pytest.mark.parametrize(("group", "case"), BY_RULES, ids=[c["q"][:48] for _, c in BY_RULES])
def test_rules_catch_what_they_are_responsible_for(group: str, case: dict[str, Any]) -> None:
    screening = screen_input_rules(case["q"])
    assert screening.risk.value == case["expect"], f"[{group}] got {screening.summary()}: {case['q']}"


@pytest.mark.parametrize(("group", "case"), BY_MODEL, ids=[c["q"][:48] for _, c in BY_MODEL])
def test_rules_do_not_guess_at_what_needs_a_reader(group: str, case: dict[str, Any]) -> None:
    """A rule that half-catches a semantic case is worse than one that leaves
    it alone: it wins nothing the model layer would not have won, and it drags
    benign phrasings in with it."""
    assert screen_input_rules(case["q"]).risk is Risk.ok, case["q"]


def test_obfuscated_attacks_are_documented_as_the_model_layer_s_job() -> None:
    """Stated so it is a decision rather than an oversight. Leetspeak, encoded
    and non-English injections defeat a pattern by construction; widening the
    patterns until they catch these is how the benign set gets eaten."""
    obfuscated = [c for _, c in BY_MODEL if c["expect"] == "block"]
    assert obfuscated, "the corpus should say out loud what the rules cannot do"
    for case in obfuscated:
        assert screen_input_rules(case["q"]).risk is Risk.ok


def test_distress_is_never_blocked_anywhere_in_the_corpus() -> None:
    for group, case in CASES:
        screening = screen_input_rules(case["q"])
        if any(f.category == "distress" for f in screening.findings):
            assert not screening.blocked, f"[{group}] blocked a distress turn: {case['q']}"


SUITES = [
    Path(".eval-reports/auto-suite.json"),
    Path(".eval-reports/web/auto-suite.json"),
]


@pytest.mark.parametrize("suite_path", SUITES, ids=[p.parent.name or "root" for p in SUITES])
def test_the_generated_eval_suites_pass_the_rules_untouched(suite_path: Path) -> None:
    """An independent benign corpus, thousands of questions wide, written for a
    different purpose entirely. If the rules are over-broad this is where it
    shows without anyone having had to imagine the phrasing."""
    if not suite_path.exists():
        pytest.skip(f"{suite_path} not generated; run `make autoeval`")
    suite = json.loads(suite_path.read_text())
    questions = [c["question"] for c in suite["cases"]] + [c["question"] for c in suite["merge_cases"]]
    raised = [(q, screen_input_rules(q).summary()) for q in questions if screen_input_rules(q).findings]
    assert not raised, f"{len(raised)} of {len(questions)} legitimate questions raised: {raised[:5]}"


# --- accuracy, as numbers rather than as a pass/fail ----------------------


def test_rule_layer_accuracy_on_the_labelled_corpus() -> None:
    """Precision and recall, stated. This corpus tuned the patterns, so a
    perfect score here means they fit their training set — it is the held-out
    check below that says whether they generalise."""
    overall, _ = score_labelled(load_scenarios())
    assert overall.total >= 190
    assert overall.precision == 1.0, f"false positives: {overall.fp_examples[:5]}"
    assert overall.recall == 1.0, f"false negatives: {overall.fn_examples[:5]}"


def test_no_blockable_category_sits_on_a_knife_edge() -> None:
    """Two properties, both about thresholds that have quietly stopped working.

    Where a lone model verdict *can* block, it must be able to do so at 0.90
    rather than only at 1.0 — the sweep caught `entitlement` and
    `impersonation` clearing 0.95 by 0.007, technically reachable and useless
    in practice because a model that hedges at all stops acting.

    Where it cannot — `injection`, deliberately, because live measurement showed
    its confidence does not separate a real attack from a customer retracting
    their own instructions — the rules must be able to block unaided, or the
    category would be one nothing can ever act on.
    """
    from api.guardrails import INPUT_POLICY, OUTPUT_POLICY

    rows = flip_points(INPUT_POLICY, "input") + flip_points(OUTPUT_POLICY, "output")
    for row in rows:
        if row["block_at"] is None:
            continue
        model_blocks = row["model_alone_blocks_from"]
        if model_blocks is None:
            assert row["rules_alone_blocks"], (
                f"{row['side']}/{row['category']} can be blocked by neither layer alone"
            )
            assert row["model_alone_flags_from"] is not None, (
                f"{row['side']}/{row['category']} gives the model no way to raise anything"
            )
            continue
        assert model_blocks <= 0.90, (
            f"{row['side']}/{row['category']} needs {model_blocks} confidence to block"
        )


# --- end to end, with the model layer simulated ---------------------------


class _Oracle:
    """A screening model that returns the corpus label at high confidence.

    It measures the *wiring* and nothing else: that a block reaches a refusal,
    that a flag reaches a route rather than a refusal, and that arbitration
    puts each turn where the label says. What the real model would actually
    say is a separate question this cannot answer, and the report says so.
    """

    name = "oracle"

    def __init__(self, labels: dict[str, dict[str, Any]]) -> None:
        self.labels = labels

    def rewrite(self, draft: Any) -> None:
        return None

    def classify(
        self, system: str, user: str, schema: dict[str, Any], *, model: str = "", max_tokens: int = 512
    ) -> dict[str, Any] | None:
        for question, case in self.labels.items():
            if question and question in user and case["by"] == "model" and case["expect"] != "ok":
                return {
                    "findings": [
                        {
                            "category": case["category"],
                            "risk": case["expect"],
                            "confidence": 0.95,
                            "detail": "simulated",
                        }
                    ]
                }
        return {"findings": []}


_GROUP_CATEGORY = {
    "advice": "advice",
    "abuse": "abuse",
    "out_of_scope": "out_of_scope",
    "distress": "distress",
    "injection": "injection",
    "obfuscation": "injection",
    "impersonation": "impersonation",
    "entitlement": "entitlement",
}


def _category_for(group: str) -> str:
    return next((v for k, v in _GROUP_CATEGORY.items() if k in group), "out_of_scope")


def test_every_labelled_turn_lands_where_its_label_says(bundle: Bundle, monkeypatch: Any) -> None:
    """The whole loop, both layers live, over the entire corpus.

    The unit tests check arbitration on synthetic findings and the calibration
    tests check the rules in isolation. Neither exercises the path a turn
    actually takes — screen, refuse or retrieve, compose, screen again — which
    is where a flag that routes nowhere or a block that still answers would
    hide.
    """
    import api.pipeline as pipeline
    from api.guardrails import Guard
    from api.pipeline import REFUSED

    labels = {c["q"]: {**c, "category": _category_for(g)} for g, c in CASES}
    guard = Guard(provider=_Oracle(labels))
    monkeypatch.setattr(pipeline, "guard_for", lambda settings, provider=None: guard)

    session_kwargs = dict(channel=Channel.direct, auth_level=AuthLevel.anonymous, today=dt.date(2026, 8, 21))
    settings = Settings(bundle_path=Path("okf"))
    wrong: list[str] = []
    seen = {"block": 0, "flag": 0, "ok": 0}

    for group, case in CASES:
        if not case["q"].strip():
            continue
        session = Session(session_id="bt", **session_kwargs)  # type: ignore[arg-type]
        envelope, _ = answer_question(bundle, case["q"], session, settings)
        refused = envelope.answer.answer == REFUSED
        seen[case["expect"]] += 1

        if (case["expect"] == "block") != refused:
            wrong.append(f"[{group}] {case['q'][:60]} — expected block={case['expect'] == 'block'}")
        # A flag routes; it never refuses. That distinction is the reason
        # `block_at=None` exists, and it only shows up end to end.
        if case["expect"] == "flag" and refused:
            wrong.append(f"[{group}] {case['q'][:60]} — a flag must not refuse the customer")
        if case["expect"] == "flag" and "advice" in group and not envelope.answer.advice_flag:
            wrong.append(f"[{group}] {case['q'][:60]} — advice flag did not reach the adviser handoff")

    assert not wrong, wrong[:10]
    # Counted from the corpus rather than hardcoded, so relabelling a family —
    # as the obfuscation cases were, once live measurement showed the model
    # cannot block `injection` unaided — does not fail this on arithmetic.
    expected = {"block": 0, "flag": 0, "ok": 0}
    for _group, case in CASES:
        if case["q"].strip():
            expected[case["expect"]] += 1
    assert seen == expected
