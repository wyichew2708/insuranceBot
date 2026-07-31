"""Failure drills as automated tests (§10.3)."""

from typing import Any

from conftest import FakeExecutor, FakeVllm
from contracts.api import ChatEventType, ChatRequest
from contracts.settings import Settings
from orchestrator.pipeline import DEGRADED_TEXT, ChatPipeline, PipelineDeps


class DeadVllm:
    async def chat_structured(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("agent endpoint down")


async def run_pipeline(message: str, agent: Any, executor: Any) -> list[Any]:
    deps = PipelineDeps(settings=Settings(), agent=agent, executor=executor)
    return [
        event
        async for event in ChatPipeline(deps).run(
            ChatRequest.model_validate(
                {"session_id": "s1", "brand": "tiq", "audience": "public", "message": message}
            )
        )
    ]


async def test_agent_down_serves_degraded_banner_with_hotline(fake_executor: FakeExecutor) -> None:
    events = await run_pipeline("Does travel cover golf?", DeadVllm(), fake_executor)
    texts = " ".join(e.text for e in events if e.type == ChatEventType.token and e.text)
    assert DEGRADED_TEXT in texts
    assert any(e.action_id == "customer-hotline" for e in events)
    assert events[-1].type == ChatEventType.done


async def test_agent_down_emergency_route_still_fully_works(fake_executor: FakeExecutor) -> None:
    events = await run_pipeline("I'm in hospital overseas, emergency!", DeadVllm(), fake_executor)
    assert any(e.action_id == "emergency-services-hotline" for e in events)
    assert events[-1].route == "emergency"


async def test_agent_down_servicing_route_degrades_not_crashes(fake_executor: FakeExecutor) -> None:
    events = await run_pipeline("How do I update my address?", DeadVllm(), fake_executor)
    texts = " ".join(e.text for e in events if e.type == ChatEventType.token and e.text)
    assert DEGRADED_TEXT in texts
    assert events[-1].type == ChatEventType.done


async def test_empty_retrieval_clarifies_never_guesses(fake_executor: FakeExecutor) -> None:
    agent = FakeVllm(
        [
            {"action": "tool", "tool": "search_kb", "args": {"query": "quantum insurance"}},
            {
                "action": "final",
                "final_text": "I couldn't find that in our knowledge base — could you rephrase?",
                "citations": [],
                "action_ids": [],
                "is_clarification": True,
            },
        ]
    )
    events = await run_pipeline("Do you cover quantum computers?", agent, fake_executor)
    texts = " ".join(e.text for e in events if e.type == ChatEventType.token and e.text)
    assert "couldn't find" in texts
    assert not any(e.type == ChatEventType.citation for e in events)
