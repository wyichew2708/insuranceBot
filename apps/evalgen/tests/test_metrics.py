from evalgen.metrics import CaseResult, Coverage, f1, percentile, score


def result(**kw: object) -> CaseResult:
    base: dict[str, object] = {"case_id": "c", "category": "figure", "question": "q", "passed": True}
    base.update(kw)
    return CaseResult.model_validate(base)


def test_percentile_edges() -> None:
    values = [1.0, 2.0, 3.0, 4.0, 100.0]
    assert percentile(values, 0.0) == 1.0
    assert percentile(values, 1.0) == 100.0
    assert percentile([], 0.5) == 0.0
    assert percentile([5.0], 0.9) == 5.0


def test_f1() -> None:
    assert f1(1.0, 1.0) == 1.0
    assert f1(0.0, 0.0) == 0.0
    assert round(f1(0.5, 1.0), 3) == 0.667


def test_recall_at_k_uses_rank_order() -> None:
    r = result(relevant_pages=["b"], admitted_pages=["a", "b", "c"])
    assert r.recall_at(1) == 0.0
    assert r.recall_at(3) == 1.0
    assert result().recall_at(3) is None


def test_score_aggregates_every_family() -> None:
    results = [
        result(
            citation_precision=1.0,
            citation_recall=1.0,
            figure_exact=True,
            figure_row_ids=["r1"],
            latency_ms=10,
            delivered=True,
            confidence=0.9,
        ),
        result(
            passed=False,
            category="entitlement",
            citation_precision=0.0,
            citation_recall=0.0,
            figure_exact=False,
            unbound_figures=1,
            latency_ms=30,
            delivered=False,
            gate_failures=["numeric-binding"],
        ),
    ]
    coverage = Coverage(total_pages=4, total_rows=4)
    coverage.pages_loaded.update({"a", "b"})
    coverage.pages_cited.add("a")
    coverage.rows_exercised.add("r1")

    report = score(
        results,
        [{"passed": True}],
        coverage,
        suite="s",
        bundle="b",
        generated_at="t",
        ran_at="t",
        wall_clock_s=1.0,
    )
    assert report.accuracy == 0.5
    assert report.citation_precision == 0.5
    assert report.figure_exact_match == 0.5
    assert report.unbound_figure_count == 1
    assert report.entitlement_leaks == 1
    assert report.gate_failures == {"numeric-binding": 1}
    assert report.delivery_rate == 0.5
    assert report.merge_rate == 1.0
    assert report.latency_p50 > 0
    assert report.page_reach_rate == 0.5
    assert report.row_coverage == 0.25
    assert report.throughput_per_s == 2.0


def test_empty_run_does_not_divide_by_zero() -> None:
    report = score([], [], Coverage(), suite="s", bundle="b", generated_at="t", ran_at="t", wall_clock_s=0.0)
    assert report.accuracy == 0.0
    assert report.total_cases == 0
