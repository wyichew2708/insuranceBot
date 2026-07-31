"""Agent loop skeleton (§8.3).

v1 ships the deterministic harness: state, step budget, loop detection, and
the guided-procedure path. The plan/act model calls plug into `planner` —
in Phase 0 a stub planner answers without tools (echo mode), so the whole
loop is exercisable end-to-end and in tests. LangGraph migration is a
drop-in swap of `run_agent_loop` internals.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from orchestrator.tools import REGISTRY, ToolContext, ToolError, validate_tool_call


class PlannerDecision(BaseModel):
    """Structured output contract for the plan step."""

    action: str  # "tool" | "final"
    tool: str | None = None
    args: dict[str, Any] | None = None
    final_text: str | None = None
    citations: list[str] = []
    action_ids: list[str] = []


Planner = Callable[["AgentState"], Awaitable[PlannerDecision]]


@dataclass
class AgentState:
    session_id: str
    route: str
    message: str
    scratchpad: list[str] = field(default_factory=list)
    tool_results: list[dict[str, Any]] = field(default_factory=list)
    step_count: int = 0
    draft: PlannerDecision | None = None


@dataclass
class LoopResult:
    final_text: str
    citations: list[str]
    action_ids: list[str]
    handover: dict[str, Any] | None = None
    steps: int = 0
    forced_clarification: bool = False


CLARIFY_TEXT = (
    "I want to make sure I get this right — could you tell me a bit more about what you're looking for?"
)


async def run_agent_loop(
    state: AgentState,
    planner: Planner,
    tool_ctx: ToolContext,
    max_steps: int,
    audit: Callable[[str, dict[str, Any]], None] | None = None,
) -> LoopResult:
    seen_calls: set[str] = set()

    while state.step_count < max_steps:
        decision = await planner(state)
        state.step_count += 1

        if decision.action == "final" or decision.tool is None:
            return LoopResult(
                final_text=decision.final_text or "",
                citations=decision.citations,
                action_ids=decision.action_ids,
                steps=state.step_count,
            )

        call_key = f"{decision.tool}:{json.dumps(decision.args or {}, sort_keys=True)}"
        if call_key in seen_calls:
            # Loop detection: identical tool+args twice => force a clarifying final.
            return LoopResult(
                final_text=CLARIFY_TEXT,
                citations=[],
                action_ids=[],
                steps=state.step_count,
                forced_clarification=True,
            )
        seen_calls.add(call_key)

        try:
            args = validate_tool_call(decision.tool, decision.args or {})
            spec = REGISTRY[decision.tool]
            result = await spec.handler(args, tool_ctx)
        except ToolError as exc:
            result = {"error": str(exc)}
        except Exception as exc:  # tool backend failure is an observation, not a crash
            result = {"error": f"tool backend failure: {exc}"}

        observation = {"tool": decision.tool, "args": decision.args, "result": result}
        state.tool_results.append(observation)
        if audit:
            audit("tool_call", observation)

        if decision.tool == "escalate_human" and isinstance(result, dict) and result.get("handover"):
            return LoopResult(
                final_text="",
                citations=[],
                action_ids=[],
                handover=result,
                steps=state.step_count,
            )

    return LoopResult(
        final_text=CLARIFY_TEXT,
        citations=[],
        action_ids=[],
        steps=state.step_count,
        forced_clarification=True,
    )
