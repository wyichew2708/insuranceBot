"""FastAPI app: the serve loop plus the debug console.

The console is served from this app deliberately — a debugging tool you have to
build before you can run is a debugging tool nobody runs.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import threading
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from harness import AnswerEnvelope, AnswerRequest, Channel, TraceStore
from pydantic import BaseModel

from api.cms import configure as configure_cms
from api.cms import router as cms_router
from api.pipeline import answer_question
from api.settings import Settings, get_settings
from okf import Bundle, lint_bundle

app = FastAPI(title="Etiqa SG knowledge layer", version="0.2.0")

UI_ROOT = Path(__file__).parent.parent
CONSOLE = UI_ROOT / "console" / "index.html"
STUDIO = UI_ROOT / "studio" / "index.html"
CHAT = UI_ROOT / "chat" / "index.html"

_state: dict[str, Any] = {"bundle": None, "settings": None, "traces": TraceStore()}


def settings() -> Settings:
    if _state["settings"] is None:
        loaded = get_settings()
        if loaded.memory.lower() == "auto":
            # The served process is the one with sessions to remember.
            loaded.memory = "on"
        _state["settings"] = loaded
    current: Settings = _state["settings"]
    return current


def bundle() -> Bundle:
    if _state["bundle"] is None:
        _load()
    current: Bundle = _state["bundle"]
    return current


def _load() -> Bundle:
    from api.sor import register_bundle_policies

    loaded = Bundle.load(settings().bundle_path)
    # The SOR fixture mints one policy per (product, version, tier) the loaded
    # corpus defines. Without it a tier-specific figure has no session that
    # holds the tier, and the in-app eval run reports coverage it never had.
    register_bundle_policies(loaded)
    _state["bundle"] = loaded
    return loaded


def traces() -> TraceStore:
    store: TraceStore = _state["traces"]
    return store


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/readyz")
async def readyz() -> dict[str, Any]:
    loaded = bundle()
    return {"status": "ready", "pages": len(loaded.pages), "table_rows": len(loaded.tables)}


@app.get("/", response_class=HTMLResponse)
async def console() -> HTMLResponse:
    if not CONSOLE.exists():
        raise HTTPException(status_code=404, detail="console not built")
    return HTMLResponse(CONSOLE.read_text())


@app.get("/studio", response_class=HTMLResponse)
async def studio() -> HTMLResponse:
    """The content portal. Served from this app for the same reason the debug
    console is: a review tool you have to deploy separately is a review tool
    nobody opens."""
    if not STUDIO.exists():
        raise HTTPException(status_code=404, detail="studio not built")
    return HTMLResponse(STUDIO.read_text())


@app.get("/chat", response_class=HTMLResponse)
async def chat() -> HTMLResponse:
    """The customer-facing surface. The console at `/` shows the machinery;
    this shows what a person asking about their insurance would need — the
    answer, where it came from, and an honest signal when the bot is handing
    them over. Same `/v1/answer` contract underneath, no privileged path."""
    if not CHAT.exists():
        raise HTTPException(status_code=404, detail="chat UI not built")
    return HTMLResponse(CHAT.read_text())


@app.post("/v1/answer", response_model=AnswerEnvelope)
async def answer(req: AnswerRequest) -> AnswerEnvelope:
    envelope, trace = answer_question(bundle(), req.question, req.session, settings(), history=req.history)
    traces().put(trace)
    return envelope


#: The customer-facing name of each stage, for the progress line while the
#: turn runs. Stages not listed here are internal and show nothing.
STAGE_LABELS = {
    "ask": "Reading your question",
    "understand": "Working out which product you mean",
    "vector-search": "Finding the right pages",
    "frontmatter-filter": "Finding the right pages",
    "wiki-read": "Reading the product pages",
    "compose": "Putting the answer together",
    "generate": "Phrasing it",
    "gates": "Checking every figure and source",
}

#: Words per streamed chunk, and the pause between chunks. The text is
#: complete and verified before the first chunk leaves; this is pacing, so a
#: 90-word answer reads in as it would from a person typing quickly.
STREAM_WORDS = 3
STREAM_PAUSE_S = 0.02


def _sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _chunks(text: str, words: int = STREAM_WORDS) -> list[str]:
    parts = text.split(" ")
    return [
        " ".join(parts[i : i + words]) + (" " if i + words < len(parts) else "")
        for i in range(0, len(parts), words)
    ]


@app.post("/v1/answer/stream")
async def answer_stream(req: AnswerRequest) -> StreamingResponse:
    """The same turn as `/v1/answer`, as server-sent events.

    What is streamed, and what is not, follows from the safety line the whole
    system draws: the model phrases; the gates verify; the customer sees only
    what the gates passed. A draft streamed token by token would show text the
    entailment judge or the figure check may then refuse — and an insurance
    answer that appears and is retracted is worse than one that arrives whole.
    So the events are:

      stage   — each pipeline stage as it opens and closes, with a customer
                label, so the wait is accounted for (4 to 14 s on the Mac);
      delta   — the *verified* answer text, paced in small chunks, once the
                gates have passed it;
      done    — the full envelope, identical to `/v1/answer`'s response;
      error   — the turn failed; the detail is the exception's name.
    """
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()

    def emit(event: str, data: Any) -> None:
        loop.call_soon_threadsafe(queue.put_nowait, (event, data))

    def on_stage(name: str, phase: str, ms: float) -> None:
        label = STAGE_LABELS.get(name)
        if label:
            emit("stage", {"name": name, "phase": phase, "label": label, "ms": ms})

    def work() -> None:
        try:
            envelope, trace = answer_question(
                bundle(), req.question, req.session, settings(), history=req.history, on_stage=on_stage
            )
            traces().put(trace)
            emit("done", envelope.model_dump(mode="json"))
        except Exception as exc:  # the stream must close; the client shows the name
            emit("error", {"detail": type(exc).__name__})
        finally:
            emit("close", None)

    threading.Thread(target=work, daemon=True).start()

    async def events() -> Any:
        while True:
            event, data = await queue.get()
            if event == "close":
                break
            if event == "done":
                text = ((data.get("answer") or {}).get("answer")) or ""
                for chunk in _chunks(text):
                    yield _sse("delta", {"text": chunk})
                    await asyncio.sleep(STREAM_PAUSE_S)
            yield _sse(event, data)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/v1/sessions/{session_id}")
async def session_memory(session_id: str) -> dict[str, Any]:
    """What this session has asked and been told, one summary line per turn,
    and the rolling summary a later turn falls back on."""
    from api.pipeline import memory_for

    return memory_for(settings()).record(session_id)


class TraceSummary(BaseModel):
    trace_id: str
    question: str
    channel: str
    delivered: bool
    gates_failed: list[str]
    total_ms: float
    rag_used: bool
    pages: int


@app.get("/v1/traces", response_model=list[TraceSummary])
async def list_traces(limit: int = 25) -> list[TraceSummary]:
    return [
        TraceSummary(
            trace_id=t.trace_id,
            question=t.question,
            channel=t.channel,
            delivered=t.delivered,
            gates_failed=[g.gate for g in t.gates if g.blocking],
            total_ms=t.total_ms,
            rag_used=t.rag_used,
            pages=len(t.loaded),
        )
        for t in traces().recent(limit)
    ]


@app.get("/v1/traces/{trace_id}")
async def get_trace(trace_id: str) -> dict[str, Any]:
    trace = traces().get(trace_id)
    if trace is None:
        raise HTTPException(status_code=404, detail="unknown trace")
    payload = trace.model_dump(mode="json")
    payload["rejected"] = [c.model_dump(mode="json") for c in trace.rejected]
    payload["total_ms"] = trace.total_ms
    return payload


@app.delete("/v1/traces")
async def clear_traces() -> dict[str, str]:
    traces().clear()
    return {"status": "cleared"}


@app.get("/v1/bundle")
async def bundle_info() -> dict[str, Any]:
    loaded = bundle()
    report = lint_bundle(loaded)
    by_type: dict[str, int] = {}
    for page in loaded.pages.values():
        by_type[page.frontmatter.type.value] = by_type.get(page.frontmatter.type.value, 0) + 1
    return {
        "root": str(loaded.root),
        "manifest": {
            "name": loaded.manifest.name,
            "okf_version": loaded.manifest.okf_version,
            "underwriter": loaded.manifest.underwriter,
            "uen": loaded.manifest.uen,
        },
        "pages": len(loaded.pages),
        "by_type": by_type,
        "table_rows": len(loaded.tables),
        "lint": {
            "ok": report.ok,
            "errors": len(report.errors),
            "warnings": len(report.warnings),
        },
        "page_ids": sorted(loaded.pages),
    }


@app.get("/v1/bundle/lint")
async def bundle_lint() -> dict[str, Any]:
    report = lint_bundle(bundle())
    return {
        "ok": report.ok,
        "violations": [
            {
                "page_id": v.page_id,
                "rule": v.rule,
                "message": v.message,
                "severity": v.severity.value,
                "line": v.line,
            }
            for v in report.violations
        ],
    }


@app.get("/v1/bundle/page/{page_id:path}")
async def bundle_page(page_id: str) -> dict[str, Any]:
    page = bundle().get(page_id)
    if page is None:
        raise HTTPException(status_code=404, detail=f"no page {page_id!r}")
    return {
        "id": page.id,
        "frontmatter": page.frontmatter.model_dump(mode="json", exclude_none=True),
        "body": page.body,
        "source_path": page.source_path,
        "neighbours": bundle().neighbours(page.id),
    }


def reload() -> Bundle:
    """Reload after any write. The store owns the disk; this owns the cache,
    and two caches of the corpus would be one too many."""
    return _load()


@app.post("/v1/bundle/reload")
async def reload_bundle() -> dict[str, Any]:
    loaded = reload()
    return {"status": "reloaded", "pages": len(loaded.pages), "table_rows": len(loaded.tables)}


@app.get("/v1/fixtures")
async def fixtures() -> dict[str, Any]:
    """Everything the console needs to build its session picker."""
    from api.sor import FIXTURE_POLICIES

    return {
        "policies": [
            {
                "policy_id": p.policy_id,
                "product_id": p.product_id,
                "version": p.version,
                "tier": p.tier,
            }
            for p in FIXTURE_POLICIES.values()
        ],
        "channels": [c.value for c in Channel],
        "auth_levels": ["L0", "L1", "L2"],
        "today": dt.date.today().isoformat(),
    }


class EvalRunRequest(BaseModel):
    suite: str = "all"


@app.post("/v1/evals/run")
async def run_evals(req: EvalRunRequest) -> dict[str, Any]:
    """Loop 3 in-process, so the console can run the gate without a terminal."""
    try:
        from evals.runner import run_suites
    except ImportError as exc:  # evals live at the repo root, not in the wheel
        raise HTTPException(status_code=503, detail=f"eval suites unavailable: {exc}") from exc
    return run_suites(bundle(), settings(), suite=req.suite)


@app.get("/v1/evals")
async def list_evals() -> dict[str, Any]:
    try:
        from evals.runner import available_suites
    except ImportError:
        return {"suites": []}
    return {"suites": available_suites()}


configure_cms(bundle, settings, reload)
app.include_router(cms_router)
