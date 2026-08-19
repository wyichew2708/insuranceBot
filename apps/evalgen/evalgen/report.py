"""Report rendering.

Three artefacts from one scored run: JSON for trending, Markdown for the repo,
and a self-contained HTML report for people. The HTML carries the diagnosis,
not just the numbers — every failure is routed to one of the five Loop 4
buckets, because a metric nobody can act on is decoration.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from evalgen.metrics import CaseResult, Report

# §G Loop 4: every failure belongs to exactly one bucket, and each bucket has a
# different owner and a different fix.
BUCKETS: dict[str, tuple[str, str]] = {
    "content": ("Content gap", "No page covers it — compile one, or ingest a new source"),
    "retrieval": ("Retrieval gap", "The page exists but was not found — alias, frontmatter or taxonomy fix"),
    "tool": ("Tool gap", "The answer needed live data we do not expose — new SOR tool"),
    "harness": ("Harness gap", "Right knowledge, wrong behaviour — gate, budget or contract change"),
    "model": ("Model gap", "Everything correct, reasoning wrong — prompt or model tier"),
}


def diagnose(result: CaseResult) -> str:
    """Route a failure to the bucket that owns it."""
    joined = " ".join(result.failures).lower()
    if not result.relevant_pages and "did not cite" in joined and not result.loaded_pages:
        return "content"
    if "did not cite" in joined:
        # The page was reachable but not chosen, or not reachable at all.
        expected = set(result.expected_cites)
        return "retrieval" if not (expected & set(result.loaded_pages)) else "model"
    if "leaked" in joined or ("expected" in joined and result.gate_failures):
        return "harness"
    if "figure not bound" in joined or "missing" in joined:
        return "retrieval" if result.loaded_pages else "content"
    return "harness"


def _pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def write_json(report: Report, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(report.model_dump_json(indent=2))


def markdown(report: Report) -> str:
    failures = [r for r in report.results if not r.passed]
    buckets = Counter(diagnose(r) for r in failures)
    lines = [
        f"# Auto-evaluation report — {report.suite}",
        "",
        f"- Bundle: `{report.bundle}`",
        f"- Suite generated: {report.generated_at}",
        f"- Run: {report.ran_at}",
        f"- Cases: **{report.total_cases}** generated + {report.merge_total} merge pairs",
        "",
        "## Headline",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Answer accuracy | **{_pct(report.accuracy)}** |",
        f"| Citation F1 | {report.citation_f1:.3f} |",
        f"| Figure exact match | {_pct(report.figure_exact_match)} |",
        f"| Numeric binding integrity | {_pct(report.numeric_binding_integrity)} |",
        f"| Unbound figures | {report.unbound_figure_count} |",
        f"| Merge consistency | {report.merge_passed}/{report.merge_total} |",
        f"| Safety score | {_pct(report.safety_score)} |",
        f"| Delivery rate | {_pct(report.delivery_rate)} |",
        f"| Latency p95 | {report.latency_p95} ms |",
        "",
        "## Accuracy by category",
        "",
        "| Category | Cases | Accuracy |",
        "|---|---:|---:|",
    ]
    for category, accuracy in sorted(report.accuracy_by_category.items()):
        lines.append(f"| {category} | {report.counts_by_category[category]} | {_pct(accuracy)} |")

    lines += [
        "",
        "## Retrieval",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Recall@1 | {report.recall_at_1:.3f} |",
        f"| Recall@3 | {report.recall_at_3:.3f} |",
        f"| Recall@5 | {report.recall_at_5:.3f} |",
        f"| MRR | {report.mrr:.3f} |",
        f"| Pages loaded via graph traversal | {_pct(report.graph_contribution)} |",
        f"| Mean pages loaded | {report.mean_pages_loaded} |",
        "",
        "## Coverage",
        "",
        f"- Pages reached by at least one question: **{_pct(report.page_reach_rate)}**",
        f"- Pages cited in an answer: {_pct(report.page_citation_rate)}",
        f"- Benefit-table rows exercised: {_pct(report.row_coverage)}",
    ]
    if report.unreached_pages:
        lines.append(f"- **Unexplained gaps**: {', '.join(f'`{p}`' for p in report.unreached_pages)}")
    else:
        lines.append("- No unexplained coverage gaps")
    for entry in report.unreached_expected:
        lines.append(f"- Unreachable by design: `{entry['page']}` — {entry['why']}")
    if report.unexercised_rows_note:
        lines.append(f"- {report.unexercised_rows_note}")

    if failures:
        lines += ["", "## Failures", "", "| Case | Category | Bucket | Why |", "|---|---|---|---|"]
        for r in failures:
            bucket = BUCKETS[diagnose(r)][0]
            why = "; ".join(r.failures)[:110]
            lines.append(f"| `{r.case_id}` | {r.category} | {bucket} | {why} |")
        lines += ["", "### Bucket summary", ""]
        for key, count in buckets.most_common():
            label, fix = BUCKETS[key]
            lines.append(f"- **{label}** ({count}) — {fix}")
    else:
        lines += ["", "## Failures", "", "None."]
    return "\n".join(lines) + "\n"


def _bar_chart(pairs: list[tuple[str, float]], unit: str = "", width: int = 460) -> str:
    """Inline SVG. No chart library, no CDN — the report must open anywhere."""
    if not pairs:
        return "<p class='mute'>no data</p>"
    top = max(v for _, v in pairs) or 1.0
    row_h, gap = 22, 6
    height = len(pairs) * (row_h + gap)
    rows = []
    for i, (label, value) in enumerate(pairs):
        y = i * (row_h + gap)
        bar = max(2.0, value / top * (width - 210))
        rows.append(
            f'<text x="0" y="{y + 15}" class="lbl">{label}</text>'
            f'<rect x="150" y="{y + 3}" width="{bar:.1f}" height="{row_h - 8}" rx="3" class="bar"/>'
            f'<text x="{150 + bar + 8:.1f}" y="{y + 15}" class="val">{value:g}{unit}</text>'
        )
    return f'<svg viewBox="0 0 {width} {height}" width="100%" height="{height}">{"".join(rows)}</svg>'


def html(report: Report) -> str:
    failures = [r for r in report.results if not r.passed]
    buckets = Counter(diagnose(r) for r in failures)

    def scorecard(label: str, value: str, good: bool | None = None, note: str = "") -> str:
        tone = "" if good is None else (" good" if good else " bad")
        return (
            f'<div class="card{tone}"><div class="k">{label}</div>'
            f'<div class="v">{value}</div><div class="n">{note}</div></div>'
        )

    cat_rows = "".join(
        f"<tr><td>{c}</td><td class='num'>{report.counts_by_category[c]}</td>"
        f"<td class='num'>{_pct(a)}</td>"
        f"<td><div class='meter'><i style='width:{a * 100:.0f}%'></i></div></td></tr>"
        for c, a in sorted(report.accuracy_by_category.items())
    )
    gate_rows = (
        "".join(
            f"<tr><td class='mono'>{g}</td><td class='num'>{n}</td></tr>"
            for g, n in report.gate_failures.items()
        )
        or "<tr><td colspan=2 class='mute'>no gate blocked a delivery</td></tr>"
    )

    fail_rows = (
        "".join(
            f"<tr><td class='mono'>{r.case_id}</td><td>{r.category}</td>"
            f"<td><span class='pill'>{BUCKETS[diagnose(r)][0]}</span></td>"
            f"<td class='mute'>{'; '.join(r.failures)[:150]}</td></tr>"
            for r in failures
        )
        or "<tr><td colspan=4 class='mute'>every generated case passed</td></tr>"
    )

    bucket_rows = (
        "".join(f"<li><b>{BUCKETS[k][0]}</b> ({n}) — {BUCKETS[k][1]}</li>" for k, n in buckets.most_common())
        or "<li class='mute'>nothing to triage this run</li>"
    )

    unreached = (
        "".join(f"<code>{p}</code> " for p in report.unreached_pages)
        or "<span class='mute'>no unexplained gaps — every reachable page was reached</span>"
    )
    expected_rows = (
        "".join(
            f"<tr><td class='mono'>{e['page']}</td><td class='mute'>{e['why']}</td></tr>"
            for e in report.unreached_expected
        )
        or "<tr><td colspan=2 class='mute'>none</td></tr>"
    )
    rows_note = (
        f"<div class='note'>{report.unexercised_rows_note}</div>" if report.unexercised_rows_note else ""
    )

    latency_chart = _bar_chart(
        [
            ("p50", report.latency_p50),
            ("p90", report.latency_p90),
            ("p95", report.latency_p95),
            ("p99", report.latency_p99),
            ("max", report.latency_max),
        ],
        " ms",
    )
    stage_chart = _bar_chart(sorted(report.stage_p95.items(), key=lambda kv: -kv[1]), " ms")

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Auto-evaluation report — {report.suite}</title>
<style>
:root{{--bg:#fff;--panel:#fff;--line:#e3e8ee;--fg:#111820;--dim:#5b6773;--mute:#8b98a5;
      --accent:#0f766e;--good:#15803d;--bad:#b91c1c;--chip:#f1f5f9;
      --mono:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}}
@media (prefers-color-scheme:dark){{:root{{--bg:#0f1419;--panel:#161c23;--line:#2a3540;
      --fg:#e6edf3;--dim:#9aa7b4;--mute:#68757f;--accent:#2dd4bf;--good:#3fb950;--bad:#f85149;
      --chip:#1d252e}}}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--fg);
      font:15px/1.6 ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}}
.wrap{{max-width:1080px;margin:0 auto;padding:40px 24px 80px}}
h1{{font-size:26px;margin:0 0 6px;letter-spacing:-.2px}}
h2{{font-size:17px;margin:38px 0 12px;padding-bottom:7px;border-bottom:1px solid var(--line)}}
h3{{font-size:14px;margin:22px 0 8px;color:var(--dim)}}
.sub{{color:var(--dim);font-size:13.5px;margin-bottom:26px}}
.sub code{{background:var(--chip);padding:1px 5px;border-radius:4px;font-family:var(--mono);font-size:12px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(168px,1fr));gap:12px}}
.card{{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:13px 15px}}
.card.good{{border-left:3px solid var(--good)}} .card.bad{{border-left:3px solid var(--bad)}}
.card .k{{font-size:11px;text-transform:uppercase;letter-spacing:.7px;color:var(--mute)}}
.card .v{{font-size:23px;font-weight:640;margin:3px 0 1px;letter-spacing:-.5px}}
.card .n{{font-size:11.5px;color:var(--mute)}}
table{{width:100%;border-collapse:collapse;font-size:13.5px;margin-top:4px}}
th{{text-align:left;font-size:10.5px;text-transform:uppercase;letter-spacing:.6px;color:var(--mute);
   padding:7px 9px;border-bottom:1px solid var(--line)}}
td{{padding:7px 9px;border-bottom:1px solid var(--line);vertical-align:top}}
td.num{{text-align:right;font-variant-numeric:tabular-nums}}
.mono,code{{font-family:var(--mono);font-size:12px}}
.mute{{color:var(--mute)}}
.meter{{height:7px;background:var(--chip);border-radius:4px;overflow:hidden;min-width:90px}}
.meter i{{display:block;height:100%;background:var(--accent)}}
.pill{{background:var(--chip);border-radius:20px;padding:2px 9px;font-size:11.5px;white-space:nowrap}}
svg .lbl{{fill:var(--dim);font-size:12px}} svg .val{{fill:var(--mute);font-size:11px}}
svg .bar{{fill:var(--accent)}}
.two{{display:grid;grid-template-columns:1fr 1fr;gap:26px}}
@media(max-width:760px){{.two{{grid-template-columns:1fr}}}}
.note{{background:var(--chip);border-radius:8px;padding:12px 15px;font-size:13px;
       color:var(--dim);margin:14px 0}}
ul{{margin:8px 0;padding-left:20px}} li{{margin:4px 0;font-size:13.5px}}
</style></head><body><div class="wrap">

<h1>Auto-evaluation report</h1>
<div class="sub">
  Suite <b>{report.suite}</b> · bundle <code>{report.bundle}</code> ·
  generated {report.generated_at} · run {report.ran_at}<br>
  <b>{report.total_cases}</b> auto-generated cases + <b>{report.merge_total}</b> merge pairs,
  derived from the corpus rather than hand-written.
</div>

<div class="grid">
  {
        scorecard(
            "Answer accuracy", _pct(report.accuracy), report.accuracy >= 0.95, f"{report.total_cases} cases"
        )
    }
  {
        scorecard(
            "Citation F1",
            f"{report.citation_f1:.3f}",
            report.citation_f1 >= 0.85,
            f"P {report.citation_precision:.2f} / R {report.citation_recall:.2f}",
        )
    }
  {
        scorecard(
            "Figure exact match",
            _pct(report.figure_exact_match),
            report.figure_exact_match >= 0.95,
            "value bound to the right row",
        )
    }
  {
        scorecard(
            "Unbound figures",
            str(report.unbound_figure_count),
            report.unbound_figure_count == 0,
            "numbers with no source",
        )
    }
  {
        scorecard(
            "Safety",
            _pct(report.safety_score),
            report.safety_score >= 0.99,
            f"{report.entitlement_leaks} entitlement leaks",
        )
    }
  {
        scorecard(
            "Merge consistency",
            f"{report.merge_passed}/{report.merge_total}",
            report.merge_passed == report.merge_total,
            "same facts, both brands",
        )
    }
  {scorecard("Delivery rate", _pct(report.delivery_rate), None, f"{_pct(report.block_rate)} gate-blocked")}
  {
        scorecard(
            "Latency p95",
            f"{report.latency_p95} ms",
            report.latency_p95 < 6000,
            f"p50 {report.latency_p50} ms",
        )
    }
</div>

<h2>Accuracy by category</h2>
<table><tr><th>Category</th><th class="num">Cases</th><th class="num">Accuracy</th><th></th></tr>
{cat_rows}</table>

<h2>Correctness</h2>
<div class="two">
<div>
<table>
<tr><th>Metric</th><th class="num">Value</th></tr>
<tr><td>Citation precision</td><td class="num">{report.citation_precision:.3f}</td></tr>
<tr><td>Citation recall</td><td class="num">{report.citation_recall:.3f}</td></tr>
<tr><td>Citation F1</td><td class="num">{report.citation_f1:.3f}</td></tr>
<tr><td>Figure exact match</td><td class="num">{_pct(report.figure_exact_match)}</td></tr>
<tr><td>Numeric binding integrity</td><td class="num">{_pct(report.numeric_binding_integrity)}</td></tr>
<tr><td>Mean confidence</td><td class="num">{report.mean_confidence:.2f}</td></tr>
<tr><td>Answers declaring something unresolved</td><td class="num">{_pct(report.unresolved_rate)}</td></tr>
</table>
<div class="note">Read precision with care: each generated case pins the
<i>minimal</i> expected source, so an answer that also cites a supporting page
scores as imprecise even though the extra citation is correct. Recall is the
unambiguous half — it is the share of expected sources the answer actually
cited.</div>
</div>
<div>
<h3>Gate blocks</h3>
<table><tr><th>Gate</th><th class="num">Blocks</th></tr>{gate_rows}</table>
<div class="note">A block is not a failure. Several generated cases — a customer on a
superseded policy version, for instance — are expected to be refused, and the
suite asserts that they are.</div>
</div>
</div>

<h2>Retrieval</h2>
<div class="two">
<div><table>
<tr><th>Metric</th><th class="num">Value</th></tr>
<tr><td>Recall@1</td><td class="num">{report.recall_at_1:.3f}</td></tr>
<tr><td>Recall@3</td><td class="num">{report.recall_at_3:.3f}</td></tr>
<tr><td>Recall@5</td><td class="num">{report.recall_at_5:.3f}</td></tr>
<tr><td>MRR</td><td class="num">{report.mrr:.3f}</td></tr>
<tr><td>Pages reached via graph traversal</td><td class="num">{_pct(report.graph_contribution)}</td></tr>
<tr><td>Mean pages loaded per turn</td><td class="num">{report.mean_pages_loaded}</td></tr>
</table></div>
<div><div class="note">Recall@1 below Recall@3 is expected and healthy here: the
frontmatter filter deliberately admits a small candidate set and lets graph
traversal pull in the linked benefit and exclusion pages, rather than betting
the answer on a single top hit.</div></div>
</div>

<h2>Performance</h2>
<div class="two">
<div><h3>End-to-end latency</h3>{latency_chart}
<div class="note">Throughput {report.throughput_per_s}/s in-process. These figures
exclude model inference — the deterministic composer is the offline path.</div></div>
<div><h3>p95 by stage</h3>{stage_chart}</div>
</div>

<h2>Corpus coverage</h2>
<div class="grid">
  {scorecard("Pages reached", _pct(report.page_reach_rate), None, "by at least one question")}
  {scorecard("Pages cited", _pct(report.page_citation_rate), None, "in a delivered answer")}
  {
        scorecard(
            "Table rows exercised",
            _pct(report.row_coverage),
            None,
            f"{len(report.unexercised_rows)} untouched",
        )
    }
</div>
<h3>Unexplained coverage gaps</h3>
<p>{unreached}</p>
{rows_note}
<h3>Unreachable by design</h3>
<table><tr><th>Page</th><th>Why</th></tr>{expected_rows}</table>
<div class="note">A page nothing asks about is a content-ops signal; a page that
questions ask about but retrieval never reaches is a taxonomy signal; a page
that is expired or unapproved is <i>supposed</i> to be unreachable. Separating
the three is what keeps this section actionable.</div>

<h2>Failures and triage</h2>
<table><tr><th>Case</th><th>Category</th><th>Bucket</th><th>Why</th></tr>{fail_rows}</table>
<h3>Where the work goes</h3>
<ul>{bucket_rows}</ul>

</div></body></html>
"""


def write_all(report: Report, out_dir: Path) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "json": out_dir / "auto-eval.json",
        "markdown": out_dir / "auto-eval.md",
        "html": out_dir / "auto-eval.html",
    }
    write_json(report, paths["json"])
    paths["markdown"].write_text(markdown(report))
    paths["html"].write_text(html(report))
    return paths
