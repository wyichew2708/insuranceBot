"""The auto-eval pipeline end to end, plus the report rendering."""

import datetime as dt
import json
from pathlib import Path

import pytest
from api.settings import Settings
from evalgen.generator import generate, per_product_counts, per_product_facts
from evalgen.metrics import CaseResult
from evalgen.report import diagnose, html, markdown, write_all
from evalgen.runner import build_session, run_case, run_merge_case, run_suite
from evalgen.schema import SessionSpec

from okf import Bundle

BUNDLE_ROOT = Path(__file__).resolve().parents[3] / "okf"
TODAY = dt.date(2026, 8, 19)


@pytest.fixture(scope="module")
def report(bundle: Bundle):  # type: ignore[no-untyped-def]
    suite = generate(bundle, BUNDLE_ROOT, TODAY)
    return run_suite(bundle, Settings(bundle_path=BUNDLE_ROOT), suite)


KNOWN = json.loads((Path(__file__).parent / "known-findings.json").read_text())["findings"]
KNOWN_CASES = {case for finding in KNOWN.values() for case in finding["cases"]}


def test_no_case_fails_that_is_not_a_recorded_finding(report) -> None:  # type: ignore[no-untyped-def]
    """The suite asks each fact many ways, so one defect fails many cases.

    Asserting a flat 100% would mean either fixing four open defects before the
    suite could land or deleting the questions that expose them. Instead the
    open findings are recorded case by case in `known-findings.json`: anything
    failing outside that list is a regression and fails the build.
    """
    new = sorted(
        (r.case_id, r.failures) for r in report.results if not r.passed and r.case_id not in KNOWN_CASES
    )
    assert not new, f"regressions outside the recorded findings: {new}"


def test_recorded_findings_are_still_real(report) -> None:  # type: ignore[no-untyped-def]
    """The other half of the ratchet. A recorded finding that now passes has
    been fixed, and the record has to shrink to say so — otherwise the file
    silently accumulates cases nobody has looked at in months."""
    passing = sorted(r.case_id for r in report.results if r.passed and r.case_id in KNOWN_CASES)
    assert not passing, (
        f"these recorded findings now pass and should be removed from known-findings.json: {passing}"
    )


def test_every_product_carries_a_full_question_set(bundle: Bundle) -> None:
    """The floor the suite is built to hold. A product the corpus barely
    describes still has to be asked about properly, or its coverage number is
    an average over questions nobody chose."""
    suite = generate(bundle, BUNDLE_ROOT, TODAY)
    counts = per_product_counts(suite)
    assert counts, "no case was attributed to any product"
    thin = {product: n for product, n in counts.items() if n < 100}
    assert not thin, f"under 100 cases: {thin}"

    # Questions without facts behind them are paraphrase depth, not coverage.
    facts = per_product_facts(suite)
    assert all(facts.get(product, 0) >= 20 for product in counts), facts


def test_failures_are_split_by_what_they_risk(report) -> None:  # type: ignore[no-untyped-def]
    """A refusal and a wrong answer are both failures and are not both
    shippable, so the report has to keep them apart."""
    assert report.unsafe_failures + report.miss_failures == sum(not r.passed for r in report.results)
    misses = [r for r in report.results if r.severity == "miss"]
    assert all(r.handoff or not r.delivered for r in misses)


def test_no_number_is_ever_unbound(report) -> None:  # type: ignore[no-untyped-def]
    # The whole numeric-binding design collapses if this is not zero.
    assert report.unbound_figure_count == 0
    assert report.numeric_binding_integrity == 1.0


def test_no_entitlement_leaks(report) -> None:  # type: ignore[no-untyped-def]
    """Customer data is the one thing with no baseline. A leak is never a
    recorded finding — it fails the build the day it appears."""
    assert report.entitlement_leaks == 0


def test_merge_consistency_holds_across_routes(report) -> None:  # type: ignore[no-untyped-def]
    assert report.merge_total > 0
    assert report.merge_passed == report.merge_total


def test_retrieval_metrics_are_ranked_not_alphabetical(report) -> None:  # type: ignore[no-untyped-def]
    # Scoring alphabetical order would silently understate MRR.
    assert report.recall_at_3 >= report.recall_at_1
    assert report.mrr > 0.5


def test_coverage_has_no_unexplained_gaps(report) -> None:  # type: ignore[no-untyped-def]
    assert report.unreached_pages == [], f"unreachable pages: {report.unreached_pages}"


def test_session_construction_rejects_unknown_policy() -> None:
    with pytest.raises(ValueError):
        build_session(SessionSpec(auth_level="L2", policy_id="NOPE-1"), "case")


def test_single_case_scores_its_evidence(bundle: Bundle) -> None:
    suite = generate(bundle, BUNDLE_ROOT, TODAY)
    case = next(c for c in suite.cases if c.category.value == "figure")
    result, trace = run_case(bundle, Settings(bundle_path=BUNDLE_ROOT), case)
    assert result.passed, result.failures
    assert result.figure_row_ids
    assert result.citation_precision is not None
    assert result.latency_ms > 0
    assert trace.candidates


def test_merge_case_reports_channel_specific_links(bundle: Bundle) -> None:
    suite = generate(bundle, BUNDLE_ROOT, TODAY)
    outcome = run_merge_case(bundle, Settings(bundle_path=BUNDLE_ROOT), suite.merge_cases[0])
    assert outcome["passed"], outcome["failures"]
    assert outcome["loaded"]


def test_report_artifacts_render(report, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    paths = write_all(report, tmp_path)
    assert set(paths) == {"json", "markdown", "html"}
    payload = json.loads(paths["json"].read_text())
    assert payload["total_cases"] == report.total_cases

    md = markdown(report)
    assert "Answer accuracy" in md and "Retrieval" in md

    page = html(report)
    assert page.startswith("<!doctype html>")
    assert "Knowledge Layer Evals" in page
    assert "<script" not in page  # a report should not need to execute anything

    # The viewer has three theme states: explicit light, explicit dark, and
    # unstamped (system). All three must resolve, or the report renders one
    # theme's text on the other theme's ground.
    assert ':root:not([data-theme="light"])' in page
    assert ':root[data-theme="dark"]' in page
    assert "prefers-color-scheme" in page
    assert "background:var(--ground)" in page  # never inherit the host ground


def test_failures_route_to_a_loop4_bucket() -> None:
    retrieval = CaseResult(
        case_id="c",
        category="figure",
        question="q",
        passed=False,
        failures=["did not cite product/general/travel"],
        expected_cites=["product/general/travel"],
        loaded_pages=["concept/excess"],
    )
    assert diagnose(retrieval) == "retrieval"

    model = CaseResult(
        case_id="c",
        category="figure",
        question="q",
        passed=False,
        failures=["did not cite product/general/travel"],
        expected_cites=["product/general/travel"],
        loaded_pages=["product/general/travel"],
    )
    assert diagnose(model) == "model"
