"""Orchestrator service: /v1/chat SSE endpoint.

Phase 0 behaviour: emergency route short-circuits with the hotline action;
everything else flows through the agent loop with a stub echo planner so the
gateway -> orchestrator -> stream path is real end-to-end. Later phases only
replace the planner and enable retrieval-backed tools.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

from contracts.api import ChatEvent, ChatEventType, ChatRequest
from contracts.settings import Settings, get_settings
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from insurance_clients.observability import get_tracer

from orchestrator.loop import AgentState, PlannerDecision, run_agent_loop
from orchestrator.router import Route, route_message
from orchestrator.tools import ToolContext

app = FastAPI(title="orchestrator")

EMERGENCY_ACTION_ID = "emergency-services-hotline"
EMERGENCY_BLOCK_ID = "common/escalation/overseas-emergency"


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/readyz")
async def readyz() -> dict[str, str]:
    return {"status": "ready"}


def _sse(event: ChatEvent) -> str:
    return f"data: {event.model_dump_json(exclude_none=True)}\n\n"


async def _echo_planner(state: AgentState) -> PlannerDecision:
    """Phase 0 stub: no tools, no facts. Replaced by the model planner in Phase 3."""
    return PlannerDecision(
        action="final",
        final_text=(
            "Thanks for your message. I'm the assistant for your insurer and I'm "
            f"still being set up. You said: {state.message!r}"
        ),
        citations=[],
        action_ids=[],
    )


async def _chat_events(req: ChatRequest, settings: Settings) -> AsyncIterator[str]:
    tracer = get_tracer()
    trace_id = tracer.new_trace_id()
    decision = route_message(req.message)

    with tracer.span(trace_id, "router", route=decision.route.value):
        pass

    if decision.route == Route.emergency:
        # Hard product rule 5: hotline first, before any retrieval.
        yield _sse(
            ChatEvent(
                type=ChatEventType.token,
                text=(
                    "If you are facing an emergency overseas, please call our "
                    "Emergency Services Hotline right away — it is available around "
                    "the clock, every day."
                ),
            )
        )
        yield _sse(ChatEvent(type=ChatEventType.action, action_id=EMERGENCY_ACTION_ID))
        yield _sse(ChatEvent(type=ChatEventType.citation, chunk_id=EMERGENCY_BLOCK_ID))
        yield _sse(ChatEvent(type=ChatEventType.done, route=decision.route.value, trace_id=trace_id))
        return

    state = AgentState(session_id=req.session_id, route=decision.route.value, message=req.message)
    tool_ctx = ToolContext(
        settings=settings,
        session_id=req.session_id,
        brand=req.brand.value,
        audience=req.audience.value,
        trace_id=trace_id,
    )
    result = await run_agent_loop(state, _echo_planner, tool_ctx, settings.agent_max_steps)

    if result.handover is not None:
        yield _sse(ChatEvent(type=ChatEventType.handover, payload=result.handover))
    else:
        yield _sse(ChatEvent(type=ChatEventType.token, text=result.final_text))
        for chunk_id in result.citations:
            yield _sse(ChatEvent(type=ChatEventType.citation, chunk_id=chunk_id))
        for action_id in result.action_ids:
            yield _sse(ChatEvent(type=ChatEventType.action, action_id=action_id))
    yield _sse(ChatEvent(type=ChatEventType.done, route=decision.route.value, trace_id=trace_id))


@app.post("/v1/chat")
async def chat(req: ChatRequest) -> StreamingResponse:
    settings = get_settings()
    return StreamingResponse(_chat_events(req, settings), media_type="text/event-stream")


def parse_sse_line(line: str) -> ChatEvent | None:
    """Helper for clients/tests: parse one SSE data line into a ChatEvent."""
    line = line.strip()
    if not line.startswith("data: "):
        return None
    return ChatEvent.model_validate(json.loads(line[len("data: ") :]))
