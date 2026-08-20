"""Content portal API (§G Loop 2, with a person in the loop).

The portal's job is to make the corpus editable without making it untrustworthy.
Three rules hold that together, and they live here rather than in the UI:

1. **A write is linted before it lands.** The candidate is validated against a
   shadow bundle; errors refuse the write and come back as violations the
   editor can act on.
2. **A scan proposes, a person disposes.** Scans stage into their own directory
   and produce suggestions; adopting one writes a `draft`, never an `approved`.
3. **Approval is a signature.** Promoting a page records who signed it off and
   when it must be looked at again.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from api import scan as scanner
from api.integrations import probe, registry
from api.settings import Settings
from api.store import ContentStore, StoreError, _as_violation_dicts, taxonomy
from okf import Bundle, lint_bundle

router = APIRouter(prefix="/v1/cms", tags=["content"])

# Injected by main.py so the router shares the app's single bundle instance.
_ctx: dict[str, Any] = {"bundle": None, "settings": None, "reload": None}


def configure(bundle_getter: Any, settings_getter: Any, reload_fn: Any) -> None:
    _ctx.update({"bundle": bundle_getter, "settings": settings_getter, "reload": reload_fn})


def _bundle() -> Bundle:
    result: Bundle = _ctx["bundle"]()
    return result


def _settings() -> Settings:
    result: Settings = _ctx["settings"]()
    return result


def _store() -> ContentStore:
    return ContentStore(Path(_settings().bundle_path))


def _reload() -> Bundle:
    result: Bundle = _ctx["reload"]()
    return result


def _fail(exc: StoreError) -> HTTPException:
    return HTTPException(
        status_code=422,
        detail={"message": str(exc), "violations": _as_violation_dicts(exc.violations)},
    )


# --- reading the corpus -----------------------------------------------------


@router.get("/overview")
async def overview() -> dict[str, Any]:
    """The landing view: what is published, what is not, and what needs a human."""
    bundle = _bundle()
    today = dt.date.today()
    report = lint_bundle(bundle)
    pages = list(bundle.pages.values())

    def count(predicate: Any) -> int:
        return sum(1 for p in pages if predicate(p))

    latest = scanner.REGISTRY.latest_done()
    return {
        "bundle": {
            "root": str(bundle.root),
            "name": bundle.manifest.name,
            "underwriter": bundle.manifest.underwriter,
            "fixture": bundle.manifest.fixture,
            "pages": len(pages),
            "table_rows": len(bundle.tables),
        },
        "health": {
            "approved": count(lambda p: p.frontmatter.status.value == "approved"),
            "draft": count(lambda p: p.frontmatter.status.value == "draft"),
            "in_review": count(lambda p: p.frontmatter.status.value == "in_review"),
            "deprecated": count(lambda p: p.frontmatter.status.value == "deprecated"),
            "retrievable": count(lambda p: bundle.retrievable(p, today)),
            "review_overdue": count(lambda p: p.frontmatter.is_review_overdue(today)),
            "regulated_advice": count(lambda p: p.frontmatter.regulated_advice),
            "lint_errors": len(report.errors),
            "lint_warnings": len(report.warnings),
        },
        "taxonomy": taxonomy(bundle),
        "last_scan": latest.as_dict(include_suggestions=False) if latest else None,
        "scans": [j.as_dict(include_suggestions=False) for j in scanner.REGISTRY.recent(8)],
        "today": today.isoformat(),
    }


@router.get("/pages")
async def list_pages(
    q: str = "",
    type: str = "",
    status: str = "",
    tag: str = "",
    line_of_business: str = "",
    needs_attention: bool = False,
) -> dict[str, Any]:
    bundle = _bundle()
    today = dt.date.today()
    rows = _store().summaries(bundle, today)
    needle = q.strip().lower()

    def keep(row: dict[str, Any]) -> bool:
        if (
            needle
            and needle
            not in " ".join(
                [row["id"], row["title"], " ".join(row["aliases"]), " ".join(row["tags"])]
            ).lower()
        ):
            return False
        if type and row["type"] != type:
            return False
        if status and row["status"] != status:
            return False
        if tag and tag not in row["tags"]:
            return False
        if line_of_business and row["line_of_business"] != line_of_business:
            return False
        if needs_attention:
            return bool(row["review_overdue"] or row["status"] != "approved" or not row["retrievable"])
        return True

    filtered = [r for r in rows if keep(r)]
    return {"total": len(rows), "count": len(filtered), "pages": filtered}


@router.get("/pages/{page_id:path}")
async def read_page(page_id: str) -> dict[str, Any]:
    bundle = _bundle()
    page = bundle.get(page_id)
    if page is None:
        raise HTTPException(status_code=404, detail=f"no page {page_id!r}")
    violations = [v for v in lint_bundle(bundle).violations if v.page_id == page_id]
    rows = [
        row.__dict__ | {"row_id": row.row_id, "rendered": row.rendered()}
        for row in bundle.tables.rows
        if row.product == bundle.product_key(page)
    ]
    return {
        "id": page.id,
        "frontmatter": page.frontmatter.model_dump(mode="json", exclude_none=True),
        "body": page.body,
        "source_path": page.source_path,
        "product_key": bundle.product_key(page),
        "neighbours": bundle.neighbours(page.id),
        "backlinks": sorted(p.id for p in bundle.pages.values() if page.id in bundle.neighbours(p.id)),
        "violations": _as_violation_dicts(violations),
        "table_rows": rows,
        "retrievable": bundle.retrievable(page, dt.date.today()),
    }


# --- writing ----------------------------------------------------------------


class PageWrite(BaseModel):
    frontmatter: dict[str, Any]
    body: str
    actor: str = "portal"


class LintRequest(BaseModel):
    frontmatter: dict[str, Any]
    body: str


@router.post("/lint")
async def lint_candidate(req: LintRequest) -> dict[str, Any]:
    """Validate without saving — what the editor calls as you type, so the
    rules are something you work with rather than something you hit."""
    store = _store()
    try:
        page = store.build_page(req.frontmatter, req.body)
    except StoreError as exc:
        return {"ok": False, "violations": [], "message": str(exc)}
    violations = store.validate(_bundle(), page)
    errors = [v for v in violations if v.severity.value == "error"]
    return {
        "ok": not errors,
        "violations": _as_violation_dicts(violations),
        "message": "" if not errors else f"{len(errors)} error(s) block saving",
    }


@router.put("/pages/{page_id:path}")
async def save_page(page_id: str, req: PageWrite) -> dict[str, Any]:
    if req.frontmatter.get("id") != page_id:
        raise HTTPException(status_code=400, detail="frontmatter id must match the path")
    try:
        result = _store().save(_bundle(), req.frontmatter, req.body, actor=req.actor)
    except StoreError as exc:
        raise _fail(exc) from exc
    _reload()
    return {"saved": result.page_id, "path": result.path, "warnings": _as_violation_dicts(result.violations)}


class CustomPage(BaseModel):
    id: str
    title: str
    type: str = "concept"
    body: str
    source_text: str = ""
    tags: list[str] = Field(default_factory=list)
    aliases: list[str] = Field(default_factory=list)
    line_of_business: str = ""
    actor: str = "portal"


@router.post("/pages")
async def create_page(req: CustomPage) -> dict[str, Any]:
    try:
        result = _store().create_custom(
            _bundle(),
            page_id=req.id,
            title=req.title,
            page_type=req.type,
            body=req.body,
            source_text=req.source_text,
            actor=req.actor,
            tags=req.tags,
            aliases=req.aliases,
            line_of_business=req.line_of_business or None,
        )
    except StoreError as exc:
        raise _fail(exc) from exc
    _reload()
    return {"created": result.page_id, "path": result.path}


class StatusChange(BaseModel):
    status: str
    actor: str = "portal"
    note: str = ""
    review_months: int = 3


@router.post("/pages/{page_id:path}/status")
async def change_status(page_id: str, req: StatusChange) -> dict[str, Any]:
    try:
        result = _store().set_status(
            _bundle(), page_id, req.status, actor=req.actor, review_months=req.review_months, note=req.note
        )
    except StoreError as exc:
        raise _fail(exc) from exc
    bundle = _reload()
    page = bundle.get(page_id)
    return {
        "page_id": result.page_id,
        "status": req.status,
        "retrievable": bool(page and bundle.retrievable(page, dt.date.today())),
    }


class TagChange(BaseModel):
    tags: list[str]
    actor: str = "portal"


@router.post("/pages/{page_id:path}/tags")
async def change_tags(page_id: str, req: TagChange) -> dict[str, Any]:
    try:
        _store().set_tags(_bundle(), page_id, req.tags, actor=req.actor)
    except StoreError as exc:
        raise _fail(exc) from exc
    bundle = _reload()
    page = bundle.get(page_id)
    tags = (page.frontmatter.model_extra or {}).get("tags", []) if page else []
    return {"page_id": page_id, "tags": tags}


@router.delete("/pages/{page_id:path}")
async def delete_page(page_id: str) -> dict[str, Any]:
    try:
        result = _store().delete(_bundle(), page_id)
    except StoreError as exc:
        raise _fail(exc) from exc
    _reload()
    return result


# --- scanning ---------------------------------------------------------------


class ScanRequest(BaseModel):
    hosts: list[str] = Field(default_factory=list)
    fixture: bool = False


@router.post("/scan")
async def start_scan(req: ScanRequest) -> dict[str, Any]:
    hosts = req.hosts or (scanner.FIXTURE_HOSTS if req.fixture else scanner.REAL_HOSTS)
    job = scanner.REGISTRY.create(hosts=hosts, fixture=req.fixture)
    job.note("queued")
    scanner.start(job, Path(_settings().bundle_path))
    return job.as_dict(include_suggestions=False)


@router.get("/scan")
async def list_scans() -> dict[str, Any]:
    return {"scans": [j.as_dict(include_suggestions=False) for j in scanner.REGISTRY.recent(20)]}


@router.get("/scan/{job_id}")
async def scan_status(job_id: str) -> dict[str, Any]:
    job = scanner.REGISTRY.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="unknown scan")
    return job.as_dict()


class SuggestionAction(BaseModel):
    actor: str = "portal"


@router.post("/scan/{job_id}/suggestions/{suggestion_id}/apply")
async def apply_suggestion(job_id: str, suggestion_id: str, req: SuggestionAction) -> dict[str, Any]:
    job = scanner.REGISTRY.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="unknown scan")
    suggestion = job.find(suggestion_id)
    if suggestion is None:
        raise HTTPException(status_code=404, detail="unknown suggestion")

    store = _store()
    staged = Path(job.staged_root)
    try:
        if suggestion.action == "adopt-page":
            result = store.adopt(_bundle(), staged, suggestion.page_id, actor=req.actor)
            outcome = f"{result.page_id} written as a draft"
        elif suggestion.action == "adopt-tables":
            path = store.adopt_tables(staged, suggestion.product)
            outcome = f"benefit table replaced: {path}"
        else:
            raise HTTPException(
                status_code=400,
                detail=f"{suggestion.kind} is for a human to act on; there is nothing safe to apply",
            )
    except StoreError as exc:
        raise _fail(exc) from exc

    suggestion.applied = True
    job.note(f"applied {suggestion_id}: {outcome}")
    _reload()
    return {"applied": suggestion_id, "outcome": outcome}


@router.post("/scan/{job_id}/suggestions/{suggestion_id}/dismiss")
async def dismiss_suggestion(job_id: str, suggestion_id: str) -> dict[str, Any]:
    job = scanner.REGISTRY.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="unknown scan")
    suggestion = job.find(suggestion_id)
    if suggestion is None:
        raise HTTPException(status_code=404, detail="unknown suggestion")
    suggestion.dismissed = True
    job.note(f"dismissed {suggestion_id}")
    return {"dismissed": suggestion_id}


# --- integrations -----------------------------------------------------------


@router.get("/integrations")
async def list_integrations() -> dict[str, Any]:
    return {"integrations": [i.as_dict() for i in registry(_settings())]}


@router.post("/integrations/{name}/test")
async def test_integration(name: str) -> dict[str, Any]:
    return await probe(name, _settings())


# --- evaluation -------------------------------------------------------------

_EVAL_JOBS: dict[str, dict[str, Any]] = {}


class EvalRequest(BaseModel):
    mode: str = "generated"  # generated | curated
    suite: str = "all"


async def _run_eval_job(job: dict[str, Any], mode: str, suite: str) -> None:
    """Loop 3 in-process. The portal is where a content change is made, so it
    is where the consequence of that change should be measurable — publishing
    a page and then going to find a terminal is how the eval step gets
    skipped."""
    import time

    started = time.perf_counter()
    try:
        bundle = _reload()
        settings = _settings()
        if mode == "curated":
            from evals.runner import run_suites

            job["result"] = run_suites(bundle, settings, suite=suite)
            job["headline"] = {
                "cases": job["result"]["total"],
                "accuracy": job["result"]["pass_rate"],
            }
        else:
            from evalgen.generator import generate
            from evalgen.runner import run_suite

            generated = generate(bundle, bundle.root)
            report = run_suite(bundle, settings, generated)
            job["result"] = report.model_dump(mode="json")
            job["headline"] = {
                "cases": report.total_cases,
                "accuracy": report.accuracy,
                "citation_f1": report.citation_f1,
                "figure_exact_match": report.figure_exact_match,
                "safety_score": report.safety_score,
                "numeric_binding": report.numeric_binding_integrity,
                "merge": f"{report.merge_passed}/{report.merge_total}",
                "latency_p95": report.latency_p95,
                "recall_at_1": report.recall_at_1,
                "page_reach_rate": report.page_reach_rate,
                "row_coverage": report.row_coverage,
            }
        job["state"] = "done"
    except Exception as exc:
        job["state"] = "failed"
        job["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        job["elapsed_s"] = round(time.perf_counter() - started, 2)


@router.post("/evals/run")
async def run_eval(req: EvalRequest) -> dict[str, Any]:
    import asyncio
    import uuid

    job: dict[str, Any] = {
        "id": uuid.uuid4().hex[:12],
        "mode": req.mode,
        "suite": req.suite,
        "state": "running",
        "started_at": dt.datetime.now().isoformat(timespec="seconds"),
        "result": None,
        "headline": {},
        "error": "",
    }
    _EVAL_JOBS[job["id"]] = job
    asyncio.get_running_loop().create_task(_run_eval_job(job, req.mode, req.suite))
    return {k: v for k, v in job.items() if k != "result"}


@router.get("/evals/{job_id}")
async def eval_status(job_id: str) -> dict[str, Any]:
    job = _EVAL_JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="unknown eval run")
    return job
