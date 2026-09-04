"""Loop 3 — Evaluate (§G).

Runs on every publish and on a schedule; gates promotion. Any drop in
groundedness, numeric binding or safety blocks the publish, and the merge
consistency suite is the mechanical guarantee that channel-as-attribute held.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

import yaml
from api.llm import provider_for
from api.pipeline import answer_question
from api.settings import Settings
from harness import AuthLevel, Channel, PolicyContext, Session

from okf import Bundle

SUITES_DIR = Path(__file__).parent / "suites"


def available_suites() -> list[str]:
    return sorted(p.stem for p in SUITES_DIR.glob("*.yaml"))


def _session(spec: dict[str, Any], case_id: str) -> Session:
    from api.sor import FIXTURE_POLICIES

    policy = None
    policy_id = spec.get("policy_id")
    if policy_id:
        fixture = FIXTURE_POLICIES.get(policy_id)
        if fixture is None:
            raise ValueError(f"{case_id}: unknown fixture policy {policy_id!r}")
        policy = PolicyContext(
            policy_id=fixture.policy_id,
            product_id=fixture.product_id,
            version=fixture.version,
            tier=fixture.tier,
        )
    today = spec.get("today")
    return Session(
        session_id=f"eval-{case_id}",
        channel=Channel(spec.get("channel", "unknown")),
        auth_level=AuthLevel(spec.get("auth_level", "L0")),
        policy=policy,
        today=dt.date.fromisoformat(today) if today else dt.date.today(),
    )


def _check(case: dict[str, Any], envelope: Any, trace: Any, bundle: Bundle | None = None) -> list[str]:
    expect = case.get("expect") or {}
    failures: list[str] = []
    answer = envelope.answer
    text = answer.answer.lower()

    # Asserting a *product* rather than a page id. A field-test case cares that
    # a burglary question was answered about home insurance, not about which of
    # the product's five child pages happened to carry the sentence — pinning
    # the page id would make the suite fail on an improvement.
    wanted_product = expect.get("cite_product")
    if wanted_product and bundle is not None:
        # A list means any of them will do. Several questions have more than one
        # right answer — a flooded flat is served by any of the home products —
        # and naming one of them turns an improvement into a failure.
        wanted = {wanted_product} if isinstance(wanted_product, str) else set(wanted_product)
        cited = set()
        for claim in answer.claims:
            page = bundle.get(claim.source_id)
            if page is not None:
                cited.add(bundle.product_key(page))
        if not (cited & wanted):
            failures.append(f"cited products {sorted(cited) or '[]'}, expected one of {sorted(wanted)}")

    if "clarifying" in expect and answer.clarifying != expect["clarifying"]:
        failures.append(f"clarifying={answer.clarifying}, expected {expect['clarifying']}")

    if "delivered" in expect and envelope.delivered != expect["delivered"]:
        failures.append(f"delivered={envelope.delivered}, expected {expect['delivered']}")

    for page_id in expect.get("must_cite", []) or []:
        if not any(c.source_id == page_id for c in answer.claims):
            failures.append(f"missing citation {page_id}")

    for needle in expect.get("must_contain", []) or []:
        if needle.lower() not in text:
            failures.append(f"missing {needle!r}")

    for needle in expect.get("must_not_contain", []) or []:
        if needle.lower() in text:
            failures.append(f"must not contain {needle!r}")

    if expect.get("figures_bound"):
        unbound = [f.label for f in answer.figures if not f.is_bound]
        if unbound:
            failures.append(f"unbound figures {unbound}")
        if not answer.figures:
            failures.append("expected bound figures, got none")

    if "handoff" in expect and answer.handoff != expect["handoff"]:
        failures.append(f"handoff={answer.handoff}, expected {expect['handoff']}")

    if "advice_flag" in expect and answer.advice_flag != expect["advice_flag"]:
        failures.append(f"advice_flag={answer.advice_flag}, expected {expect['advice_flag']}")

    for gate_name in expect.get("gates_pass", []) or []:
        result = next((g for g in envelope.gates if g.gate == gate_name), None)
        if result is None:
            failures.append(f"gate {gate_name} did not run")
        elif result.blocking:
            failures.append(f"gate {gate_name} failed: {result.detail}")

    for gate_name in expect.get("gates_fail", []) or []:
        result = next((g for g in envelope.gates if g.gate == gate_name), None)
        if result is None or not result.blocking:
            failures.append(f"gate {gate_name} was expected to block but did not")

    if expect.get("rag_used") is not None and trace.rag_used != expect["rag_used"]:
        failures.append(f"rag_used={trace.rag_used}, expected {expect['rag_used']}")

    if expect.get("unresolved_nonempty") and not answer.unresolved:
        failures.append("expected a non-empty unresolved list")

    return failures


def _run_standard(bundle: Bundle, settings: Settings, case: dict[str, Any]) -> dict[str, Any]:
    session = _session(case.get("session") or {}, str(case["id"]))
    # `turns` is one conversation; `expect` is asserted against the last turn.
    # Half of what the field test found only appears on a follow-up — a subject
    # carried from an earlier turn, or lost by it — and a suite that can only
    # ask one question cannot see any of it.
    turns = [str(t) for t in (case.get("turns") or [])] or [case["question"]]
    if not turns:
        raise ValueError(f"{case['id']}: no question and no turns")
    history: list[str] = []
    envelope, trace = answer_question(bundle, turns[0], session, settings, history=[])
    for turn in turns[1:]:
        history.append(turns[len(history)])
        envelope, trace = answer_question(bundle, turn, session, settings, history=list(history))
    failures = _check(case, envelope, trace, bundle)
    return {
        "id": case["id"],
        "passed": not failures,
        "failures": failures,
        "trace_id": trace.trace_id,
        "answer": envelope.answer.answer,
        "composer": trace.composer,
        # What the turn actually did, alongside what was expected. `failures`
        # says a case missed; this says *how*, and for a case that owed a
        # handoff the difference matters more than the pass rate: answering a
        # question about a suspicious email with a termination clause and
        # asking which product it is about are both wrong, and only one of
        # them is dangerous.
        "observed": {
            "delivered": envelope.delivered,
            "handoff": envelope.answer.handoff,
            "clarifying": envelope.answer.clarifying,
            "advice_flag": envelope.answer.advice_flag,
            "smalltalk": envelope.answer.smalltalk,
            "rag_used": trace.rag_used,
            "claims": len(envelope.answer.claims),
        },
    }


def _run_merge_consistency(bundle: Bundle, settings: Settings, case: dict[str, Any]) -> dict[str, Any]:
    """The same question asked on each distribution route must return the same
    facts and only different deep links (§G Loop 3).

    Routes differ in how a customer buys, never in what they bought."""
    failures: list[str] = []
    results = []
    for variant in case["variants"]:
        session = _session(variant, f"{case['id']}-{variant.get('channel', 'unknown')}")
        envelope, trace = answer_question(bundle, variant["question"], session, settings)
        results.append((variant, envelope, trace))

    _, baseline, _ = results[0]
    baseline_figures = {(f.label, f.text, f.table_row_id) for f in baseline.answer.figures}
    baseline_claims = {(c.source_id, c.locator) for c in baseline.answer.claims}

    for _variant, envelope, _trace in results[1:]:
        figures = {(f.label, f.text, f.table_row_id) for f in envelope.answer.figures}
        claims = {(c.source_id, c.locator) for c in envelope.answer.claims}
        if figures != baseline_figures:
            failures.append(f"figures differ between channels: {baseline_figures ^ figures}")
        if claims != baseline_claims:
            failures.append(f"claims differ between channels: {baseline_claims ^ claims}")
        base_link = baseline.answer.channel_render.landing if baseline.answer.channel_render else None
        link = envelope.answer.channel_render.landing if envelope.answer.channel_render else None
        if base_link and link and base_link == link:
            failures.append("deep links are identical across channels; channel binding did not apply")

    return {
        "id": case["id"],
        "passed": not failures,
        "failures": failures,
        "trace_id": "",
        "answer": baseline.answer.answer,
        "composer": results[0][2].composer,
    }


#: Labels the taxonomy puts on a case and the runner carries through to the
#: report. Not read by `_check` — they say what a case *is*, so a score can be
#: read as "claims questions on motor products are weak" rather than as one
#: number over 1,356 cases.
LABELS = ("section", "journey", "intent", "entities", "contract", "product", "brand", "lifecycle")


def load_suite(path: Path) -> tuple[str, list[dict[str, Any]]]:
    """A suite file, and the bundle it is written for.

    Two shapes, because one of them is older than the other. A bare list is a
    suite that runs against whatever bundle it is given — the seed suites,
    which are written against `okf`. A mapping with `bundle` and `cases` names
    the corpus it was generated from, and `run_suites` skips it when a
    different one is loaded: `faq-customer` and `conversation` are both derived
    from `okf-real` and every case in them fails against the seed bundle,
    which is not a finding about the bot.
    """
    data = yaml.safe_load(path.read_text()) or []
    if isinstance(data, dict):
        return str(data.get("bundle", "")), list(data.get("cases") or [])
    return "", list(data)


def run_suite_file(bundle: Bundle, settings: Settings, path: Path) -> dict[str, Any]:
    _, cases = load_suite(path)
    results = []
    for case in cases:
        try:
            if case.get("type") == "merge-consistency":
                results.append(_run_merge_consistency(bundle, settings, case))
            else:
                results.append(_run_standard(bundle, settings, case))
        except Exception as exc:  # a crashing case is a failing case, not a crashing run
            results.append(
                {"id": case.get("id", "?"), "passed": False, "failures": [f"error: {exc}"], "answer": ""}
            )
        results[-1].update({label: case[label] for label in LABELS if label in case})
    passed = sum(1 for r in results if r["passed"])
    return {
        "suite": path.stem,
        "total": len(results),
        "passed": passed,
        "pass_rate": passed / len(results) if results else 0.0,
        "results": results,
    }


def run_suites(bundle: Bundle, settings: Settings, suite: str = "all") -> dict[str, Any]:
    names = available_suites() if suite == "all" else [suite]
    paths = [SUITES_DIR / f"{name}.yaml" for name in names]
    if suite == "all":
        # A suite generated from another corpus is skipped rather than failed.
        # Naming it explicitly still runs it, so `--suite conversation` against
        # the seed bundle is a thing you can do on purpose.
        served = bundle.root.name
        paths = [p for p in paths if load_suite(p)[0] in ("", served)]
    suites = [run_suite_file(bundle, settings, p) for p in paths]
    total = sum(s["total"] for s in suites)
    passed = sum(s["passed"] for s in suites)
    return {
        "suites": suites,
        "total": total,
        "passed": passed,
        "pass_rate": passed / total if total else 0.0,
    }


def main() -> None:
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Run the eval suites (Loop 3)")
    parser.add_argument("--suite", default="all")
    parser.add_argument("--bundle", type=Path, default=Path("okf"))
    parser.add_argument("--gate", type=float, default=1.0, help="minimum pass rate")
    parser.add_argument(
        "--allow-fallback",
        action="store_true",
        help="score cases the requested provider did not actually serve",
    )
    args = parser.parse_args()

    settings = Settings(bundle_path=args.bundle)
    report = run_suites(Bundle.load(args.bundle), settings, args.suite)

    for suite in report["suites"]:
        print(f"\n=== {suite['suite']} — {suite['passed']}/{suite['total']}")
        for result in suite["results"]:
            mark = "PASS" if result["passed"] else "FAIL"
            print(f"  {mark}  {result['id']}")
            for failure in result["failures"]:
                print(f"          - {failure}")
    # Which engine actually answered. This matters more than it looks: a
    # provider that is rate-limited, timing out, or holding a bad key falls
    # back to the deterministic composer *silently and per case*, so a run
    # that never reached the model still scores 100%. An eval that cannot
    # tell you it did not run is worse than no eval.
    engines: dict[str, int] = {}
    for suite in report["suites"]:
        for result in suite["results"]:
            engines[result.get("composer") or "unknown"] = (
                engines.get(result.get("composer") or "unknown", 0) + 1
            )
    print("\nanswered by:")
    for engine, count in sorted(engines.items(), key=lambda kv: -kv[1]):
        print(f"  {count:3}  {engine}")

    requested = provider_for(settings).name
    fell_back = sum(
        count
        for engine, count in engines.items()
        if requested != "deterministic" and not engine.startswith(f"{requested}:")
    )

    print(f"\noverall {report['passed']}/{report['total']} ({report['pass_rate']:.1%}), gate {args.gate:.0%}")
    if fell_back and not args.allow_fallback:
        print(
            f"\nFAILED: {requested!r} was requested but {fell_back} case(s) were served by "
            f"the deterministic composer. Those cases measured the fallback, not the model — "
            f"scoring them would report a pass rate the model never earned.\n"
            f"Fix the provider, or pass --allow-fallback to score anyway."
        )
        sys.exit(2)
    if report["pass_rate"] < args.gate:
        sys.exit(1)


if __name__ == "__main__":
    main()
