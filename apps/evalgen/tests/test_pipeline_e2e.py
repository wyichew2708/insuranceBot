"""The auto-eval pipeline end to end, plus the report rendering."""

import datetime as dt
import json
from pathlib import Path

import pytest
from api.settings import Settings
from evalgen.generator import generate
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


def test_the_bot_passes_its_own_generated_suite(report) -> None:  # type: ignore[no-untyped-def]
    failing = [(r.case_id, r.failures) for r in report.results if not r.passed]
    assert report.accuracy == 1.0, f"regressions: {failing}"


def test_no_number_is_ever_unbound(report) -> None:  # type: ignore[no-untyped-def]
    # The whole numeric-binding design collapses if this is not zero.
    assert report.unbound_figure_count == 0
    assert report.numeric_binding_integrity == 1.0


def test_no_entitlement_leaks(report) -> None:  # type: ignore[no-untyped-def]
    assert report.entitlement_leaks == 0
    assert report.safety_score == 1.0


def test_merge_consistency_holds_across_brands(report) -> None:  # type: ignore[no-untyped-def]
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
