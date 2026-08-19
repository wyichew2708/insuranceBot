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
    rows_note = f"<p class='note'>{report.unexercised_rows_note}</p>" if report.unexercised_rows_note else ""

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
<title>Knowledge Layer Evals</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?\
family=Fraunces:opsz,wght@9..144,500;9..144,600&\
family=Public+Sans:wght@400;500;600&\
family=JetBrains+Mono:wght@400;500&display=swap">
<style>
/* Light is the base palette; the dark blocks below redefine only tokens, so
   every component keeps resolving in all three viewer states. */
:root{{
  --ground:#fbfcfc; --panel:#ffffff; --sunk:#f2f6f6; --line:#dde5e6;
  --ink:#0f1a1c; --dim:#54646a; --mute:#8a999e;
  --accent:#0d7d72; --good:#15803d; --bad:#b4231f;
  --display:"Fraunces",Georgia,"Times New Roman",serif;
  --body:"Public Sans",ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif;
  --mono:"JetBrains Mono",ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
}}
@media (prefers-color-scheme:dark){{
  :root:not([data-theme="light"]){{
    --ground:#0e1416; --panel:#161e21; --sunk:#1b2427; --line:#2a3639;
    --ink:#e4edee; --dim:#9dadb2; --mute:#6b7c81;
    --accent:#37d6c3; --good:#4ac26a; --bad:#f2695f;
  }}
}}
:root[data-theme="dark"]{{
  --ground:#0e1416; --panel:#161e21; --sunk:#1b2427; --line:#2a3639;
  --ink:#e4edee; --dim:#9dadb2; --mute:#6b7c81;
  --accent:#37d6c3; --good:#4ac26a; --bad:#f2695f;
}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--ground);color:var(--ink);
     font:400 15px/1.6 var(--body);-webkit-font-smoothing:antialiased}}
.wrap{{max-width:1060px;margin:0 auto;padding:56px 24px 96px;
      display:flex;flex-direction:column;gap:8px}}
h1{{font:600 34px/1.15 var(--display);margin:0;letter-spacing:-.4px;text-wrap:balance}}
h2{{font:600 19px/1.3 var(--display);margin:44px 0 0;padding-bottom:9px;
    border-bottom:1px solid var(--line);text-wrap:balance}}
h3{{font:600 11px/1.4 var(--body);margin:22px 0 0;color:var(--mute);
    text-transform:uppercase;letter-spacing:.9px}}
.lede{{color:var(--dim);font-size:14px;margin:10px 0 20px;max-width:62ch}}
.lede b{{color:var(--ink);font-weight:600}}
.lede code{{font:400 12.5px var(--mono);background:var(--sunk);padding:1px 6px;border-radius:4px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(172px,1fr));gap:12px;margin-top:12px}}
.card{{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:14px 16px}}
.card.good{{box-shadow:inset 3px 0 0 var(--good)}}
.card.bad{{box-shadow:inset 3px 0 0 var(--bad)}}
.card .k{{font-size:10.5px;text-transform:uppercase;letter-spacing:.9px;color:var(--mute);
         font-weight:600}}
.card .v{{font:600 25px/1.15 var(--display);margin:6px 0 3px;letter-spacing:-.4px;
         font-variant-numeric:tabular-nums}}
.card .n{{font-size:11.5px;color:var(--mute)}}
.scroll{{overflow-x:auto;margin-top:10px}}
table{{width:100%;border-collapse:collapse;font-size:13.5px;min-width:min(100%,440px)}}
th{{text-align:left;font-size:10.5px;text-transform:uppercase;letter-spacing:.8px;
   color:var(--mute);font-weight:600;padding:8px 10px;border-bottom:1px solid var(--line);
   white-space:nowrap}}
td{{padding:8px 10px;border-bottom:1px solid var(--line);vertical-align:top}}
tr:last-child td{{border-bottom:0}}
td.num{{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}}
.mono,code{{font:400 12px var(--mono)}}
.mute{{color:var(--mute)}}
.meter{{height:6px;background:var(--sunk);border-radius:3px;overflow:hidden;min-width:96px}}
.meter i{{display:block;height:100%;background:var(--accent)}}
.pill{{background:var(--sunk);border-radius:20px;padding:3px 10px;font-size:11.5px;
      white-space:nowrap;color:var(--dim)}}
svg .lbl{{fill:var(--dim);font:400 12px var(--body)}}
svg .val{{fill:var(--mute);font:400 11px var(--mono)}}
svg .bar{{fill:var(--accent)}}
.two{{display:grid;grid-template-columns:1fr 1fr;gap:28px;align-items:start}}
@media(max-width:760px){{.two{{grid-template-columns:1fr}}}}
.note{{background:var(--sunk);border-radius:9px;padding:13px 16px;font-size:13px;
      color:var(--dim);margin:14px 0 0;max-width:62ch}}
.note i{{color:var(--ink);font-style:italic}}
ul{{margin:10px 0 0;padding-left:20px;display:flex;flex-direction:column;gap:5px}}
li{{font-size:13.5px;color:var(--dim)}} li b{{color:var(--ink)}}
p{{margin:10px 0 0}}
</style></head><body><div class="wrap">

<h1>Knowledge Layer Evals</h1>
<p class="lede">
  Suite <b>{report.suite}</b> over bundle <code>{report.bundle}</code> ·
  generated {report.generated_at} · run {report.ran_at}<br>
  <b>{report.total_cases}</b> cases and <b>{report.merge_total}</b> merge pairs,
  each derived from the corpus itself — a benefit-table row, an authored alias,
  an effective window — rather than hand-written.
</p>

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
            "bound to the right row",
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
<div class="scroll"><table>
<tr><th>Category</th><th class="num">Cases</th><th class="num">Accuracy</th><th></th></tr>
{cat_rows}</table></div>

<h2>Correctness</h2>
<div class="two">
<div>
<div class="scroll"><table>
<tr><th>Metric</th><th class="num">Value</th></tr>
<tr><td>Citation precision</td><td class="num">{report.citation_precision:.3f}</td></tr>
<tr><td>Citation recall</td><td class="num">{report.citation_recall:.3f}</td></tr>
<tr><td>Citation F1</td><td class="num">{report.citation_f1:.3f}</td></tr>
<tr><td>Figure exact match</td><td class="num">{_pct(report.figure_exact_match)}</td></tr>
<tr><td>Numeric binding integrity</td><td class="num">{_pct(report.numeric_binding_integrity)}</td></tr>
<tr><td>Mean confidence</td><td class="num">{report.mean_confidence:.2f}</td></tr>
<tr><td>Declared something unresolved</td><td class="num">{_pct(report.unresolved_rate)}</td></tr>
</table></div>
<p class="note">Read precision with care: each case pins the <i>minimal</i>
expected source, so an answer that also cites a supporting page scores as
imprecise even though the extra citation is correct. Recall is the unambiguous
half — the share of expected sources the answer actually cited.</p>
</div>
<div>
<h3>Gate blocks</h3>
<div class="scroll"><table><tr><th>Gate</th><th class="num">Blocks</th></tr>{gate_rows}</table></div>
<p class="note">A block is not a failure. Several generated cases — a customer
on a superseded policy version, for instance — are expected to be refused, and
the suite asserts that they are.</p>
</div>
</div>

<h2>Retrieval</h2>
<div class="two">
<div><div class="scroll"><table>
<tr><th>Metric</th><th class="num">Value</th></tr>
<tr><td>Recall@1</td><td class="num">{report.recall_at_1:.3f}</td></tr>
<tr><td>Recall@3</td><td class="num">{report.recall_at_3:.3f}</td></tr>
<tr><td>Recall@5</td><td class="num">{report.recall_at_5:.3f}</td></tr>
<tr><td>MRR</td><td class="num">{report.mrr:.3f}</td></tr>
<tr><td>Reached via graph traversal</td><td class="num">{_pct(report.graph_contribution)}</td></tr>
<tr><td>Mean pages loaded per turn</td><td class="num">{report.mean_pages_loaded}</td></tr>
</table></div></div>
<div><p class="note">Recall@1 sitting below Recall@3 is expected here, not a
weakness: the frontmatter filter deliberately admits a small candidate set and
lets graph traversal pull in the linked benefit and exclusion pages, rather
than betting the answer on a single top hit.</p></div>
</div>

<h2>Performance</h2>
<div class="two">
<div><h3>End-to-end latency</h3>{latency_chart}
<p class="note">Throughput {report.throughput_per_s}/s in-process. These figures
exclude model inference — the deterministic composer is the offline path.</p></div>
<div><h3>p95 by stage</h3>{stage_chart}</div>
</div>

<h2>Corpus coverage</h2>
<div class="grid">
  {scorecard("Pages reached", _pct(report.page_reach_rate), None, "by at least one question")}
  {scorecard("Pages cited", _pct(report.page_citation_rate), None, "in a delivered answer")}
  {scorecard("Rows exercised", _pct(report.row_coverage), None, f"{len(report.unexercised_rows)} untouched")}
</div>
<h3>Unexplained gaps</h3>
<p>{unreached}</p>
{rows_note}
<h3>Unreachable by design</h3>
<div class="scroll"><table><tr><th>Page</th><th>Why</th></tr>{expected_rows}</table></div>
<p class="note">A page nothing asks about is a content-ops signal; a page that
questions ask about but retrieval never reaches is a taxonomy signal; a page
that is expired or unapproved is <i>supposed</i> to be unreachable. Separating
the three is what keeps this section actionable.</p>

<h2>Failures and triage</h2>
<div class="scroll"><table>
<tr><th>Case</th><th>Category</th><th>Bucket</th><th>Why</th></tr>{fail_rows}</table></div>
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
