"""Run generated cases against the serve loop and score each one.

Runs in-process against `answer_question`, so a full sweep is seconds rather
than minutes and needs no server. The trace is the scoring input: it carries
the admitted candidates (for retrieval metrics), the resolved figures with
their row ids (for numeric accuracy), the gate verdicts and the stage timings.
"""

from __future__ import annotations

import datetime as dt
import time
from pathlib import Path

from api.pipeline import answer_question
from api.settings import Settings
from api.sor import FIXTURE_POLICIES, register_bundle_policies
from harness import AnswerEnvelope, AuthLevel, Channel, PolicyContext, Session, Trace

from evalgen.metrics import CaseResult, Coverage, Report, score
from evalgen.schema import GeneratedCase, MergeCase, SessionSpec, Suite
from okf import Bundle


def build_session(spec: SessionSpec, case_id: str) -> Session:
    policy = None
    if spec.policy_id:
        fixture = FIXTURE_POLICIES.get(spec.policy_id)
        if fixture is None:
            raise ValueError(f"{case_id}: unknown fixture policy {spec.policy_id!r}")
        policy = PolicyContext(
            policy_id=fixture.policy_id,
            product_id=fixture.product_id,
            version=fixture.version,
            tier=fixture.tier,
        )
    return Session(
        session_id=f"auto-{case_id}",
        channel=Channel(spec.channel),
        auth_level=AuthLevel(spec.auth_level),
        policy=policy,
        today=spec.today or dt.date.today(),
    )


def _check(case: GeneratedCase, envelope: AnswerEnvelope, trace: Trace) -> list[str]:
    expect = case.expect
    answer = envelope.answer
    text = answer.answer.lower()
    failures: list[str] = []

    for page_id in expect.must_cite:
        if not any(c.source_id == page_id for c in answer.claims):
            failures.append(f"did not cite {page_id}")
    for needle in expect.must_contain:
        if needle.lower() not in text:
            failures.append(f"missing {needle!r}")
    for needle in expect.must_not_contain:
        if needle and needle.lower() in text:
            failures.append(f"leaked {needle!r}")
    for row_id in expect.expect_row_ids:
        if row_id not in {f.table_row_id for f in answer.figures if f.table_row_id}:
            failures.append(f"figure not bound to {row_id}")
    if expect.expect_delivered is not None and envelope.delivered != expect.expect_delivered:
        failures.append(f"delivered={envelope.delivered}, expected {expect.expect_delivered}")
    if expect.expect_handoff is not None and answer.handoff != expect.expect_handoff:
        failures.append(f"handoff={answer.handoff}, expected {expect.expect_handoff}")
    if expect.expect_advice_flag is not None and answer.advice_flag != expect.expect_advice_flag:
        failures.append(f"advice_flag={answer.advice_flag}, expected {expect.expect_advice_flag}")
    if expect.expect_rag is not None and trace.rag_used != expect.expect_rag:
        failures.append(f"rag_used={trace.rag_used}, expected {expect.expect_rag}")
    for gate in expect.expect_gate_fail:
        if not any(g.gate == gate and g.blocking for g in envelope.gates):
            failures.append(f"gate {gate} was expected to block")
    return failures


def run_case(bundle: Bundle, settings: Settings, case: GeneratedCase) -> tuple[CaseResult, Trace]:
    session = build_session(case.session, case.id)
    started = time.perf_counter()
    envelope, trace = answer_question(bundle, case.question, session, settings)
    elapsed_ms = (time.perf_counter() - started) * 1000

    answer = envelope.answer
    cited = sorted({c.source_id for c in answer.claims})
    expected = case.expect.must_cite
    row_ids = sorted({f.table_row_id for f in answer.figures if f.table_row_id})
    # Ranked order, not the page-id order the trace logs them in.
    admitted = [
        c.page_id
        for c in sorted(
            (c for c in trace.candidates if c.admitted),
            key=lambda c: c.rank if c.rank is not None else 10**6,
        )
    ]

    result = CaseResult(
        case_id=case.id,
        category=case.category.value,
        question=case.question,
        passed=False,
        cited=cited,
        expected_cites=expected,
        figure_row_ids=row_ids,
        expected_row_ids=case.expect.expect_row_ids,
        unbound_figures=sum(1 for f in answer.figures if not f.is_bound),
        relevant_pages=case.expect.relevant_pages,
        admitted_pages=admitted,
        loaded_pages=[p.page_id for p in trace.loaded],
        graph_pages=sum(1 for p in trace.loaded if p.via == "graph"),
        delivered=envelope.delivered,
        handoff=answer.handoff,
        advice_flag=answer.advice_flag,
        rag_used=trace.rag_used,
        gate_failures=[g.gate for g in envelope.gates if g.blocking],
        confidence=answer.confidence,
        unresolved=len(answer.unresolved),
        latency_ms=round(elapsed_ms, 2),
        stage_ms={s.name: s.ms for s in trace.stages},
        pages_loaded=len(trace.loaded),
        answer=answer.answer,
    )
    result.failures = _check(case, envelope, trace)
    result.passed = not result.failures

    if expected:
        hits = len(set(cited) & set(expected))
        result.citation_precision = hits / len(cited) if cited else 0.0
        result.citation_recall = hits / len(expected)
    if case.expect.expect_row_ids:
        result.figure_exact = set(case.expect.expect_row_ids) <= set(row_ids)
    if case.expect.relevant_pages:
        relevant = set(case.expect.relevant_pages)
        result.reciprocal_rank = 0.0
        for position, page_id in enumerate(admitted, start=1):
            if page_id in relevant:
                result.reciprocal_rank = 1 / position
                break
    return result, trace


def run_merge_case(bundle: Bundle, settings: Settings, case: MergeCase) -> dict[str, object]:
    """Facts must be identical across brand framings; only the deep link may
    differ. This is the mechanical guarantee that the merge held (§B.1)."""
    runs = []
    for channel in case.channels:
        session = build_session(
            SessionSpec(
                channel=channel,
                auth_level="L2" if case.policy_id else "L0",
                policy_id=case.policy_id,
            ),
            f"{case.id}-{channel}",
        )
        envelope, trace = answer_question(bundle, case.question, session, settings)
        runs.append((channel, envelope, trace))

    failures: list[str] = []
    _, baseline, _ = runs[0]
    if not baseline.answer.claims and not baseline.answer.figures:
        failures.append("neither channel produced an answer; consistency is vacuous")
    base_figs = {(f.label, f.text, f.table_row_id) for f in baseline.answer.figures}
    base_claims = {(c.source_id, c.locator) for c in baseline.answer.claims}
    base_link = baseline.answer.channel_render.landing if baseline.answer.channel_render else None

    for channel, envelope, _trace in runs[1:]:
        figs = {(f.label, f.text, f.table_row_id) for f in envelope.answer.figures}
        claims = {(c.source_id, c.locator) for c in envelope.answer.claims}
        link = envelope.answer.channel_render.landing if envelope.answer.channel_render else None
        if figs != base_figs:
            failures.append(f"{channel}: figures differ ({base_figs ^ figs})")
        if claims != base_claims:
            failures.append(f"{channel}: claims differ ({base_claims ^ claims})")
        if base_link and link and base_link == link:
            failures.append(f"{channel}: deep link did not vary by channel")

    return {
        "id": case.id,
        "question": case.question,
        "channels": case.channels,
        "passed": not failures,
        "failures": failures,
        "answer": baseline.answer.answer,
        "loaded": sorted({p.page_id for _c, _e, tr in runs for p in tr.loaded}),
    }


def run_suite(bundle: Bundle, settings: Settings, suite: Suite) -> Report:
    started = time.perf_counter()
    results: list[CaseResult] = []
    coverage = Coverage(total_pages=len(bundle.pages), total_rows=len(bundle.tables))

    for case in suite.cases:
        result, _trace = run_case(bundle, settings, case)
        results.append(result)
        coverage.pages_cited.update(result.cited)
        coverage.pages_loaded.update(result.loaded_pages)
        coverage.rows_exercised.update(result.figure_row_ids)

    merge_results = [run_merge_case(bundle, settings, case) for case in suite.merge_cases]
    for merge in merge_results:
        coverage.pages_loaded.update(merge.get("loaded", []))  # type: ignore[arg-type]
    wall_clock = time.perf_counter() - started

    report = score(
        results,
        merge_results,
        coverage,
        suite=suite.name,
        bundle=suite.bundle,
        generated_at=suite.generated_at,
        ran_at=dt.datetime.now().isoformat(timespec="seconds"),
        wall_clock_s=wall_clock,
    )
    today = dt.date.today()
    expected: list[dict[str, str]] = []
    gaps: list[str] = []
    for page_id in sorted(set(bundle.pages) - coverage.pages_loaded):
        page = bundle.pages[page_id]
        fm = page.frontmatter
        if not fm.is_effective_on(today):
            expected.append({"page": page_id, "why": "outside its effective window"})
        elif fm.status.value != "approved":
            expected.append({"page": page_id, "why": f"status {fm.status.value}"})
        elif fm.is_review_overdue(today):
            expected.append({"page": page_id, "why": "review overdue, demoted to RAG"})
        else:
            gaps.append(page_id)
    report.unreached_expected = expected
    report.unreached_pages = gaps

    unexercised = sorted({row.row_id for row in bundle.tables.rows} - coverage.rows_exercised)
    report.unexercised_rows = unexercised
    superseded = [r for r in unexercised if _is_superseded(bundle, r)]
    if superseded:
        report.unexercised_rows_note = (
            f"{len(superseded)} of {len(unexercised)} belong to superseded product versions, "
            "whose questions are correctly blocked by version-coherence rather than answered."
        )
    return report


def _is_superseded(bundle: Bundle, row_id: str) -> bool:
    product, version = row_id.split(":")[0], row_id.split(":")[1]
    for page in bundle.pages.values():
        if bundle.product_key(page) == product and page.frontmatter.version_in_force:
            return page.frontmatter.version_in_force != version
    return False


def load_bundle(path: Path) -> Bundle:
    bundle = Bundle.load(path)
    register_bundle_policies(bundle)
    return bundle
