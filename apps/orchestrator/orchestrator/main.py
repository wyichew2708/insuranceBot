"""Orchestrator service: /v1/chat SSE endpoint over the chat pipeline.

With VLLM_AGENT_BASE_URL configured, turns run the full harness (classifier,
model planner, verification loop). Without it (dev/tests), the pipeline
serves the deterministic routes plus a stub echo — the transport behaviour
is identical either way.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

from contracts.api import ChatEvent, ChatRequest
from contracts.settings import Settings, get_settings
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from insurance_clients.observability import get_tracer
from insurance_clients.vllm import VllmClient, VllmEndpoint

from orchestrator.pipeline import ChatPipeline, PipelineDeps, sse_format

app = FastAPI(title="orchestrator")

EMERGENCY_ACTION_ID = "emergency-services-hotline"
EMERGENCY_BLOCK_ID = "common/escalation/overseas-emergency"


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/readyz")
async def readyz() -> dict[str, str]:
    return {"status": "ready"}


def build_deps(settings: Settings, trace_id: str) -> PipelineDeps:
    agent = None
    judge = None
    if settings.vllm_agent_base_url:
        agent = VllmClient(
            VllmEndpoint(
                base_url=settings.vllm_agent_base_url,
                model=settings.vllm_agent_model,
                api_key=settings.vllm_api_key,
            )
        )
    if settings.vllm_judge_base_url:
        judge = VllmClient(
            VllmEndpoint(
                base_url=settings.vllm_judge_base_url,
                model=settings.vllm_judge_model,
                api_key=settings.vllm_api_key,
            )
        )
    return PipelineDeps(settings=settings, agent=agent, judge=judge, trace_id=trace_id)


async def _chat_events(req: ChatRequest) -> AsyncIterator[str]:
    settings = get_settings()
    tracer = get_tracer()
    trace_id = tracer.new_trace_id()
    deps = build_deps(settings, trace_id)
    pipeline = ChatPipeline(deps)
    try:
        with tracer.span(trace_id, "chat_turn", session_id=req.session_id, brand=req.brand.value):
            async for event in pipeline.run(req):
                yield sse_format(event)
    finally:
        for client in (deps.agent, deps.judge):
            if isinstance(client, VllmClient):
                await client.aclose()


@app.post("/v1/chat")
async def chat(req: ChatRequest) -> StreamingResponse:
    return StreamingResponse(_chat_events(req), media_type="text/event-stream")


def parse_sse_line(line: str) -> ChatEvent | None:
    """Helper for clients/tests: parse one SSE data line into a ChatEvent."""
    line = line.strip()
    if not line.startswith("data: "):
        return None
    return ChatEvent.model_validate(json.loads(line[len("data: ") :]))
