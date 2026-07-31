from typing import Any

from contracts.settings import Settings
from orchestrator.loop import AgentState, PlannerDecision, run_agent_loop
from orchestrator.tools import ToolContext


def ctx() -> ToolContext:
    return ToolContext(settings=Settings(), session_id="s", brand="tiq", audience="public")


def state() -> AgentState:
    return AgentState(session_id="s", route="coverage_qa", message="q")


async def test_final_on_first_step() -> None:
    async def planner(s: AgentState) -> PlannerDecision:
        return PlannerDecision(action="final", final_text="done", citations=["c1"])

    result = await run_agent_loop(state(), planner, ctx(), max_steps=6)
    assert result.final_text == "done"
    assert result.citations == ["c1"]
    assert result.steps == 1


async def test_identical_tool_call_twice_forces_clarification() -> None:
    async def planner(s: AgentState) -> PlannerDecision:
        return PlannerDecision(action="tool", tool="read_page", args={"block_id": "same"})

    result = await run_agent_loop(state(), planner, ctx(), max_steps=6)
    assert result.forced_clarification
    assert result.steps == 2


async def test_step_budget_enforced() -> None:
    calls = {"n": 0}

    async def planner(s: AgentState) -> PlannerDecision:
        calls["n"] += 1
        return PlannerDecision(action="tool", tool="read_page", args={"block_id": f"b{calls['n']}"})

    result = await run_agent_loop(state(), planner, ctx(), max_steps=3)
    assert result.forced_clarification
    assert result.steps == 3


async def test_escalate_human_ends_turn_with_handover() -> None:
    async def planner(s: AgentState) -> PlannerDecision:
        return PlannerDecision(
            action="tool", tool="escalate_human", args={"reason": "complaint", "transcript_ref": "s"}
        )

    result = await run_agent_loop(state(), planner, ctx(), max_steps=6)
    assert result.handover is not None
    assert result.handover["reason"] == "complaint"


async def test_tool_backend_failure_becomes_observation() -> None:
    seen: list[dict[str, Any]] = []

    async def planner(s: AgentState) -> PlannerDecision:
        if s.tool_results:
            return PlannerDecision(action="final", final_text="recovered")
        # retrieval service is not running in unit tests -> backend failure
        return PlannerDecision(action="tool", tool="read_page", args={"block_id": "x"})

    result = await run_agent_loop(state(), planner, ctx(), max_steps=6, audit=lambda e, p: seen.append(p))
    assert result.final_text == "recovered"
    assert "error" in seen[0]["result"]
