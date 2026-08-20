"""Scanning the websites, and checking what came back against what we publish.

This is Loop 2 with a person in it. A scan crawls the allowlisted hosts into a
**staging** bundle — never over the live corpus — compiles it, and then diffs
the result against what is published today. The output is not a new wiki; it is
a list of *suggestions* a content owner can read, each one carrying the before
and the after so the judgement stays with them.

Nothing a scan proposes lands as `approved`. A scan proposes, a person
disposes.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import re
import shutil
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from okf import Bundle, Page, Status, lint_bundle

STAGING = Path(".scan")
REAL_HOSTS = ["www.etiqa.com.sg", "www.tiq.com.sg"]
FIXTURE_HOSTS = ["www.etiqa.example", "www.tiq.example"]

# Severity ordering for the review queue: what could mislead a customer first.
SEVERITY_RANK = {"blocking": 0, "high": 1, "medium": 2, "low": 3}


# What the two sides of a suggestion's diff actually are. A website defect is
# two *websites* disagreeing with each other, not the wiki disagreeing with a
# website, and labelling it that way sends the reader to fix the wrong thing.
DIFF_LABELS = {
    "figure-drift": ("in the wiki", "on the website"),
    "figure-removed": ("in the wiki", "on the website"),
    "figure-new": ("in the wiki", "on the website"),
    "new-page": ("in the wiki", "compiled from the crawl"),
    "content-drift": ("published page", "compiled from the crawl"),
    "website-defect": ("lower authority — still published", "kept: higher authority"),
    "review-overdue": ("review was due", "today"),
}


@dataclass
class Suggestion:
    id: str
    kind: str
    severity: str
    title: str
    detail: str
    page_id: str = ""
    product: str = ""
    before: str = ""
    after: str = ""
    action: str = ""
    applied: bool = False
    dismissed: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "severity": self.severity,
            "title": self.title,
            "detail": self.detail,
            "page_id": self.page_id,
            "product": self.product,
            "before": self.before,
            "after": self.after,
            "before_label": DIFF_LABELS.get(self.kind, ("before", "after"))[0],
            "after_label": DIFF_LABELS.get(self.kind, ("before", "after"))[1],
            "action": self.action,
            "applied": self.applied,
            "dismissed": self.dismissed,
        }


@dataclass
class ScanJob:
    id: str
    hosts: list[str]
    fixture: bool
    state: str = "queued"  # queued · crawling · compiling · verifying · done · failed
    started_at: str = ""
    finished_at: str = ""
    log: list[str] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)
    suggestions: list[Suggestion] = field(default_factory=list)
    error: str = ""
    staged_root: str = ""

    def note(self, message: str) -> None:
        self.log.append(f"{dt.datetime.now().strftime('%H:%M:%S')}  {message}")

    def as_dict(self, include_suggestions: bool = True) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": self.id,
            "hosts": self.hosts,
            "fixture": self.fixture,
            "state": self.state,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "log": self.log,
            "stats": self.stats,
            "error": self.error,
            "staged_root": self.staged_root,
            "counts": self.counts(),
        }
        if include_suggestions:
            payload["suggestions"] = [s.as_dict() for s in self.sorted_suggestions()]
        return payload

    def counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for suggestion in self.suggestions:
            if suggestion.dismissed:
                continue
            counts[suggestion.kind] = counts.get(suggestion.kind, 0) + 1
        return counts

    def sorted_suggestions(self) -> list[Suggestion]:
        return sorted(
            self.suggestions,
            key=lambda s: (s.dismissed, s.applied, SEVERITY_RANK.get(s.severity, 9), s.kind, s.id),
        )

    def find(self, suggestion_id: str) -> Suggestion | None:
        return next((s for s in self.suggestions if s.id == suggestion_id), None)


class ScanRegistry:
    """In-process job registry. A deployment swaps this for a queue; the shape
    — submit, poll, act on the result — does not change."""

    def __init__(self) -> None:
        self.jobs: dict[str, ScanJob] = {}

    def create(self, hosts: list[str], fixture: bool) -> ScanJob:
        job = ScanJob(id=uuid.uuid4().hex[:12], hosts=hosts, fixture=fixture)
        self.jobs[job.id] = job
        return job

    def get(self, job_id: str) -> ScanJob | None:
        return self.jobs.get(job_id)

    def recent(self, limit: int = 10) -> list[ScanJob]:
        return sorted(self.jobs.values(), key=lambda j: j.started_at, reverse=True)[:limit]

    def latest_done(self) -> ScanJob | None:
        done = [j for j in self.jobs.values() if j.state == "done"]
        return max(done, key=lambda j: j.finished_at) if done else None


REGISTRY = ScanRegistry()


# --- the scan itself --------------------------------------------------------


async def run_scan(job: ScanJob, live_root: Path, today: dt.date | None = None) -> None:
    """Crawl → compile → verify. Errors are recorded on the job, never raised
    into the request that started it."""
    import httpx
    from compiler.wiki import CompileConfig, compile_bundle
    from crawler.crawl import USER_AGENT, CrawlConfig, crawl

    today = today or dt.date.today()
    started = time.perf_counter()
    job.started_at = dt.datetime.now().isoformat(timespec="seconds")
    staged = STAGING / job.id
    job.staged_root = str(staged)
    shutil.rmtree(staged, ignore_errors=True)

    try:
        job.state = "crawling"
        job.note(f"crawling {', '.join(job.hosts)}" + (" (synthetic fixture site)" if job.fixture else ""))
        config = CrawlConfig(
            allowlist=job.hosts,
            out_dir=staged / "raw",
            requests_per_second=200.0 if job.fixture else 1.0,
            today=today,
        )
        client: httpx.AsyncClient | None = None
        if job.fixture:
            from fixtures.synthetic_site import transport

            client = httpx.AsyncClient(
                transport=transport(), headers={"User-Agent": USER_AGENT}, follow_redirects=True
            )
        try:
            result = await crawl(config, client)
        finally:
            if client is not None:
                await client.aclose()

        pages = result.ok_pages
        job.stats = {
            "pages_crawled": len(pages),
            "documents_found": len(result.documents),
            "hosts": result.hosts,
            "skipped": result.skipped,
        }
        job.note(f"{len(pages)} pages, {len(result.documents)} documents recorded")
        if not pages:
            raise RuntimeError(
                "no pages were retrieved. If these are the live hosts, check the network egress "
                "policy — this environment refuses CONNECT to them — and confirm robots.txt "
                "allows the crawler."
            )

        job.state = "compiling"
        job.note("compiling snapshots into candidate OKF pages")
        report = compile_bundle(
            CompileConfig(source_root=staged, dest_root=staged, today=today, version=f"scan:{job.id}")
        )
        job.stats |= {
            "pages_compiled": len(report.pages),
            "benefit_rows": sum(report.tables.values()),
            "products": len(report.tables),
            "website_defects": len(report.conflicts),
        }
        job.note(f"{len(report.pages)} candidate pages, {sum(report.tables.values())} benefit rows")

        job.state = "verifying"
        job.note("comparing against the published corpus")
        live = Bundle.load(live_root)
        candidate = Bundle.load(staged)
        job.suggestions = verify(live, candidate, report, today)
        job.note(f"{len(job.suggestions)} suggestions for review")

        job.state = "done"
    except Exception as exc:
        job.state = "failed"
        job.error = str(exc)
        job.note(f"failed: {exc}")
    finally:
        job.finished_at = dt.datetime.now().isoformat(timespec="seconds")
        job.stats["elapsed_s"] = round(time.perf_counter() - started, 2)


# --- verification -----------------------------------------------------------

WHITESPACE_RE = re.compile(r"\s+")
SRC_RE = re.compile(r"\[src:[^\]]+\]")


def _comparable(page: Page) -> str:
    """Body text with references and whitespace normalised away — so a
    recompile that only re-dates a source ref is not reported as a change."""
    return WHITESPACE_RE.sub(" ", SRC_RE.sub("", page.body)).strip()


def _row_index(bundle: Bundle) -> dict[str, tuple[str, str]]:
    return {row.row_id: (row.value, row.unit) for row in bundle.tables.rows}


def verify(live: Bundle, candidate: Bundle, compile_report: Any, today: dt.date) -> list[Suggestion]:
    """Diff a freshly-scanned corpus against the published one.

    Ordered by what a customer would notice: a figure that changed on the
    website but not in the wiki is the top of the queue, because that is the
    failure where the assistant confidently quotes a number the site itself no
    longer shows.
    """
    suggestions: list[Suggestion] = []
    counter = 0

    def add(**kwargs: Any) -> None:
        nonlocal counter
        counter += 1
        suggestions.append(Suggestion(id=f"s{counter:03d}", **kwargs))

    # 1. Figures that moved. The row id is the coordinate, so this is exact.
    live_rows, new_rows = _row_index(live), _row_index(candidate)
    for row_id, (value, unit) in sorted(new_rows.items()):
        if row_id not in live_rows:
            continue
        if live_rows[row_id] != (value, unit):
            old_value, old_unit = live_rows[row_id]
            product = row_id.split(":")[0]
            add(
                kind="figure-drift",
                severity="blocking",
                title=f"{row_id.split(':')[-1]} changed on the website",
                detail=(
                    f"The site now publishes a different value for `{row_id}`. Until the benefit "
                    "table is updated the assistant will keep quoting the old one, with a citation."
                ),
                page_id="",
                product=product,
                before=f"{old_unit}{old_value}",
                after=f"{unit}{value}",
                action="adopt-tables",
            )

    # 2. Rows the site no longer publishes, and rows it has started publishing.
    missing = sorted(set(live_rows) - set(new_rows))
    added = sorted(set(new_rows) - set(live_rows))
    for row_id in missing[:40]:
        add(
            kind="figure-removed",
            severity="high",
            title=f"{row_id} is no longer on the website",
            detail=(
                "The wiki still resolves this row. Either the benefit was withdrawn — in which "
                "case the page needs updating — or the scan failed to read that table."
            ),
            product=row_id.split(":")[0],
            before=f"{live_rows[row_id][1]}{live_rows[row_id][0]}",
            after="(absent)",
            action="review",
        )
    for row_id in added[:40]:
        add(
            kind="figure-new",
            severity="medium",
            title=f"{row_id} is new on the website",
            detail="A benefit the wiki has no row for. Adopting the table makes it quotable.",
            product=row_id.split(":")[0],
            before="(absent)",
            after=f"{new_rows[row_id][1]}{new_rows[row_id][0]}",
            action="adopt-tables",
        )

    # 3. Products the site sells that the wiki does not describe at all.
    for page_id in sorted(set(candidate.pages) - set(live.pages)):
        page = candidate.pages[page_id]
        if page.frontmatter.type.value not in {"product", "journey", "promotion", "concept"}:
            continue
        add(
            kind="new-page",
            severity="high" if page.frontmatter.type.value == "product" else "medium",
            title=f"{page.frontmatter.title} is on the website but not in the wiki",
            detail=(
                "The scan compiled a candidate page from the crawled source. Adopting it adds a "
                "**draft** — it answers nothing until someone reviews and approves it."
            ),
            page_id=page_id,
            product=candidate.product_key(page),
            before="(no page)",
            after=page.body[:600],
            action="adopt-page",
        )

    # 4. Pages whose wording moved on the website.
    for page_id in sorted(set(candidate.pages) & set(live.pages)):
        live_page, new_page = live.pages[page_id], candidate.pages[page_id]
        if live_page.frontmatter.type.value == "index":
            continue
        if _comparable(live_page) == _comparable(new_page):
            continue
        add(
            kind="content-drift",
            severity="medium",
            title=f"{live_page.frontmatter.title} reads differently on the website",
            detail=(
                "The published wording no longer matches what the wiki compiled from it. Adopting "
                "replaces the page with a draft; the current approved page keeps serving until "
                "the draft is approved."
            ),
            page_id=page_id,
            product=live.product_key(live_page),
            before=live_page.body[:600],
            after=new_page.body[:600],
            action="adopt-page",
        )

    # 5. Two published surfaces disagreeing with each other (§D.2). This is a
    #    defect in a *website*, not in the wiki, and it is filed as one.
    for conflict in getattr(compile_report, "conflicts", []):
        add(
            kind="website-defect",
            severity="high",
            title=f"{conflict.product}: the two sites disagree on {conflict.coordinate}",
            detail=(
                f"Kept `{conflict.kept}` from the higher-authority source. A customer reading the "
                "other site sees a different number — raise this with whoever owns that page."
            ),
            product=conflict.product,
            before=f"{conflict.dropped}  ({conflict.dropped_source})",
            after=f"{conflict.kept}  ({conflict.kept_source})",
            action="review",
        )

    # 6. Housekeeping the scan is well placed to notice.
    for page in sorted(live.pages.values(), key=lambda p: p.id):
        fm = page.frontmatter
        if fm.is_review_overdue(today) and fm.status == Status.approved:
            add(
                kind="review-overdue",
                severity="high",
                title=f"{fm.title} is past its review date",
                detail=(
                    f"Due {fm.review_due}. Overdue pages are demoted out of wiki-first retrieval, "
                    "so this page is already answering through the RAG fallback."
                ),
                page_id=page.id,
                before=str(fm.review_due),
                after=today.isoformat(),
                action="review",
            )
        elif fm.status == Status.draft:
            add(
                kind="unapproved",
                severity="low",
                title=f"{fm.title} is still a draft",
                detail="Draft pages are not retrievable: this content answers nothing until approved.",
                page_id=page.id,
                action="review",
            )

    for violation in lint_bundle(live).errors:
        add(
            kind="lint",
            severity="blocking",
            title=f"{violation.rule} on {violation.page_id}",
            detail=violation.message,
            page_id=violation.page_id,
            action="review",
        )
    return suggestions


def start(job: ScanJob, live_root: Path) -> None:
    """Fire the scan onto the running loop and return immediately."""
    asyncio.get_running_loop().create_task(run_scan(job, live_root))
