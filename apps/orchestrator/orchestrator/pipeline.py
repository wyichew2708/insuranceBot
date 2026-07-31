"""Chat pipeline (§8): route -> gather -> draft -> verify -> stream.

All external dependencies (agent/judge clients, tool executor, redis) are
injected so the full pipeline is testable with recorded structured outputs
(§11). When no agent endpoint is configured the pipeline serves the Phase 0
echo behaviour so dev environments keep working end-to-end.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from contracts.api import ChatEvent, ChatEventType, ChatRequest, HandoverPayload
from contracts.settings import Settings

from orchestrator.audit import Auditor
from orchestrator.classifier import classify_servicing
from orchestrator.evidence import build_evidence, screen_tool_result
from orchestrator.loop import (
    AgentState,
    LoopResult,
    PlannerDecision,
    ToolExecutor,
    registry_executor,
    run_agent_loop,
)
from orchestrator.planner import StructuredChat, build_messages, make_model_planner
from orchestrator.procedures import render_procedure
from orchestrator.router import Route, route_message
from orchestrator.tools import ToolContext
from orchestrator.verification import Draft
from orchestrator.verify import verify_and_finalize

logger = logging.getLogger("orchestrator.pipeline")

EMERGENCY_ACTION_ID = "emergency-services-hotline"
EMERGENCY_BLOCK_ID = "common/escalation/overseas-emergency"
HANDOVER_STREAM = "handover.requests"

EMERGENCY_TEXT = (
    "If you are facing an emergency overseas, please call our Emergency Services "
    "Hotline right away — it is available around the clock, every day."
)

OUT_OF_SCOPE_TEXT = (
    "I'm here to help with insurance questions for our products — coverage, "
    "claims, and policy servicing. What can I help you with?"
)

DEGRADED_TEXT = (
    "I'm having trouble answering right now. You can reach us on our customer "
    "hotline, or try again in a few minutes."
)


@dataclass
class PipelineDeps:
    settings: Settings
    agent: StructuredChat | None = None
    judge: StructuredChat | None = None
    executor: ToolExecutor = registry_executor
    trace_id: str = ""


async def _screened_executor(inner: ToolExecutor, tool: str, args: dict[str, Any], ctx: ToolContext) -> Any:
    result = await inner(tool, args, ctx)
    return screen_tool_result(tool, result)


async def _emit_handover(payload: HandoverPayload, settings: Settings) -> None:
    if not settings.redis_url:
        logger.info("handover (no redis configured): %s", payload.model_dump())
        return
    try:
        import redis.asyncio as aioredis

        client = aioredis.from_url(settings.redis_url)
        await client.xadd(HANDOVER_STREAM, {"payload": payload.model_dump_json()})
        await client.aclose()
    except Exception as exc:  # handover delivery is monitored via audit log
        logger.warning("handover stream emit failed: %s", exc)


class ChatPipeline:
    def __init__(self, deps: PipelineDeps) -> None:
        self.deps = deps

    def _tool_ctx(self, req: ChatRequest) -> ToolContext:
        return ToolContext(
            settings=self.deps.settings,
            session_id=req.session_id,
            brand=req.brand.value,
            audience=req.audience.value,
            trace_id=self.deps.trace_id,
        )

    async def run(self, req: ChatRequest) -> AsyncIterator[ChatEvent]:
        auditor = Auditor(session_id=req.session_id, database_url=self.deps.settings.database_url)
        decision = route_message(req.message)
        auditor.emit("route", {"route": decision.route.value})

        try:
            if decision.route == Route.emergency:
                async for event in self._emergency():
                    yield event
            elif decision.route == Route.out_of_scope:
                yield ChatEvent(type=ChatEventType.token, text=OUT_OF_SCOPE_TEXT)
            elif self.deps.agent is None:
                async for event in self._echo(req):
                    yield event
            else:
                served = False
                if decision.route == Route.servicing:
                    async for event in self._servicing(req, auditor):
                        served = True
                        yield event
                if not served:
                    async for event in self._agentic(req, auditor):
                        yield event
            yield ChatEvent(type=ChatEventType.done, route=decision.route.value, trace_id=self.deps.trace_id)
        finally:
            await auditor.flush()

    async def _emergency(self) -> AsyncIterator[ChatEvent]:
        # Hard product rule 5: hotline first, before any retrieval.
        yield ChatEvent(type=ChatEventType.token, text=EMERGENCY_TEXT)
        yield ChatEvent(type=ChatEventType.action, action_id=EMERGENCY_ACTION_ID)
        yield ChatEvent(type=ChatEventType.citation, chunk_id=EMERGENCY_BLOCK_ID)

    async def _echo(self, req: ChatRequest) -> AsyncIterator[ChatEvent]:
        yield ChatEvent(
            type=ChatEventType.token,
            text=(
                "Thanks for your message. I'm the assistant for your insurer and I'm "
                f"still being set up. You said: {req.message!r}"
            ),
        )

    async def _servicing(self, req: ChatRequest, auditor: Auditor) -> AsyncIterator[ChatEvent]:
        """Guided-procedure flow: classify -> fetch procedure -> render template.
        Yields nothing (falls through to agentic) when classification or the
        procedure lookup is not confident."""
        assert self.deps.agent is not None
        try:
            prediction = await classify_servicing(self.deps.agent, req.message, self.deps.trace_id)
        except Exception as exc:
            logger.warning("servicing classifier unavailable: %s", exc)
            return
        if prediction is None:
            auditor.emit("servicing_classifier", {"confident": False})
            return
        auditor.emit("servicing_classifier", prediction.model_dump())

        ctx = self._tool_ctx(req)
        try:
            results = await self.deps.executor(
                "search_kb", {"query": f"procedure {prediction.intent}", "top_k": 3}, ctx
            )
        except Exception as exc:
            logger.warning("procedure search failed: %s", exc)
            return
        procedure_hit = next(
            (
                r
                for r in results or []
                if isinstance(r, dict) and (r.get("metadata") or {}).get("type") == "procedure"
            ),
            None,
        )
        if procedure_hit is None:
            auditor.emit("servicing_procedure", {"found": False, "intent": prediction.intent})
            return

        block_id = str(procedure_hit["chunk_id"]).split("#")[0]
        try:
            block = await self.deps.executor("read_page", {"block_id": block_id}, ctx)
        except Exception:
            block = {"block_id": block_id, "text": procedure_hit.get("text", ""), "metadata": {}}
        view = render_procedure(block)
        auditor.emit("tool_call", {"tool": "get_procedure", "args": {"intent": prediction.intent}})

        tool_results = [{"tool": "read_page", "args": {"block_id": block_id}, "result": block}]
        draft = Draft(text=view.text, citations=[view.citation], action_ids=view.action_ids)
        ev = build_evidence(tool_results, req.audience.value)
        outcome = await verify_and_finalize(
            draft,
            ev,
            tool_results,
            judge=None,  # template path: rule graders only, no free generation
            audit=auditor.emit,
            trace_id=self.deps.trace_id,
        )
        async for event in self._stream_draft(outcome.draft):
            yield event

    async def _agentic(self, req: ChatRequest, auditor: Auditor) -> AsyncIterator[ChatEvent]:
        assert self.deps.agent is not None
        planner = make_model_planner(self.deps.agent, self.deps.trace_id)
        state = AgentState(
            session_id=req.session_id, route=route_message(req.message).route.value, message=req.message
        )
        ctx = self._tool_ctx(req)

        async def executor(tool: str, args: dict[str, Any], c: ToolContext) -> Any:
            return await _screened_executor(self.deps.executor, tool, args, c)

        try:
            result: LoopResult = await run_agent_loop(
                state,
                planner,
                ctx,
                self.deps.settings.agent_max_steps,
                audit=auditor.emit,
                executor=executor,
            )
        except Exception as exc:
            # Failure drill (§10.3): agent endpoint down => degraded banner +
            # hotline routing; deterministic routes stay fully functional.
            logger.error("agent loop unavailable: %s", exc)
            auditor.emit("degraded", {"reason": "agent_unavailable", "error": str(exc)})
            yield ChatEvent(type=ChatEventType.token, text=DEGRADED_TEXT)
            yield ChatEvent(type=ChatEventType.action, action_id="customer-hotline")
            return

        if result.handover is not None:
            payload = HandoverPayload(
                session_id=req.session_id,
                transcript=[{"role": "user", "content": req.message}],
                summary=str(result.handover.get("reason", "")),
                reason=str(result.handover.get("reason", "")),
            )
            await _emit_handover(payload, self.deps.settings)
            auditor.emit("handover", payload.model_dump())
            yield ChatEvent(type=ChatEventType.handover, payload=payload.model_dump())
            return

        if result.forced_clarification:
            yield ChatEvent(type=ChatEventType.token, text=result.final_text)
            return

        draft = Draft(
            text=result.final_text,
            citations=result.citations,
            action_ids=result.action_ids,
            route=state.route,
            # A declared clarification skips citation-presence only; verbatim
            # and execution-claim graders still run over its text.
            is_factual=not result.is_clarification,
        )
        ev = build_evidence(state.tool_results, req.audience.value)

        async def replan(feedback: str) -> Draft | None:
            state.scratchpad.append(feedback)
            state.scratchpad.append("Respond with action=final only, correcting the issues above.")
            try:
                revised = await planner(state)
            except Exception:
                return None
            if revised.action != "final" or not revised.final_text:
                return None
            return Draft(
                text=revised.final_text,
                citations=revised.citations,
                action_ids=revised.action_ids,
                route=state.route,
            )

        outcome = await verify_and_finalize(
            draft,
            ev,
            state.tool_results,
            judge=self.deps.judge,
            replan=replan,
            max_retries=self.deps.settings.verify_max_retries,
            audit=auditor.emit,
            trace_id=self.deps.trace_id,
        )
        async for event in self._stream_draft(outcome.draft):
            yield event

    async def _stream_draft(self, draft: Draft) -> AsyncIterator[ChatEvent]:
        yield ChatEvent(type=ChatEventType.token, text=draft.text)
        for chunk_id in draft.citations:
            yield ChatEvent(type=ChatEventType.citation, chunk_id=chunk_id)
        for action_id in draft.action_ids:
            yield ChatEvent(type=ChatEventType.action, action_id=action_id)


def sse_format(event: ChatEvent) -> str:
    return f"data: {event.model_dump_json(exclude_none=True)}\n\n"


def parse_sse_line(line: str) -> ChatEvent | None:
    line = line.strip()
    if not line.startswith("data: "):
        return None
    return ChatEvent.model_validate(json.loads(line[len("data: ") :]))


__all__ = [
    "ChatPipeline",
    "PipelineDeps",
    "PlannerDecision",
    "build_messages",
    "parse_sse_line",
    "sse_format",
]
