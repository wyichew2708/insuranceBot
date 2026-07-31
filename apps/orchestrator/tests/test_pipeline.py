"""Full pipeline with recorded structured outputs (§11): router -> loop ->
verification -> stream, including the poisoned-chunk injection defence."""

from typing import Any

from conftest import FakeExecutor, FakeVllm
from contracts.api import ChatEventType, ChatRequest
from contracts.settings import Settings
from orchestrator.pipeline import ChatPipeline, PipelineDeps
from orchestrator.procedures import CANNOT_EXECUTE_LINE


def request(message: str, audience: str = "public") -> ChatRequest:
    return ChatRequest.model_validate(
        {"session_id": "s1", "brand": "tiq", "audience": audience, "message": message}
    )


async def run_pipeline(
    message: str,
    agent: FakeVllm | None,
    executor: FakeExecutor,
    judge: FakeVllm | None = None,
) -> list[Any]:
    deps = PipelineDeps(settings=Settings(), agent=agent, judge=judge, executor=executor)
    pipeline = ChatPipeline(deps)
    return [event async for event in pipeline.run(request(message))]


def texts(events: list[Any]) -> str:
    return " ".join(e.text for e in events if e.type == ChatEventType.token and e.text)


async def test_emergency_short_circuits_without_any_model_call(fake_executor: FakeExecutor) -> None:
    agent = FakeVllm([])  # would raise if consulted
    events = await run_pipeline("I'm in hospital overseas, this is an emergency", agent, fake_executor)
    assert [e.type for e in events][-1] == ChatEventType.done
    assert any(e.action_id == "emergency-services-hotline" for e in events)
    assert agent.calls == []
    assert fake_executor.calls == []


async def test_coverage_qa_happy_path_with_citation(fake_executor: FakeExecutor) -> None:
    agent = FakeVllm(
        [
            {"action": "tool", "tool": "search_kb", "args": {"query": "pre-existing conditions cover"}},
            {
                "action": "final",
                "final_text": "Pre-existing conditions are excluded unless you bought the Pre-Ex add-on.",
                "citations": ["tiq-trv/exclusions/pre-existing-conditions"],
                "action_ids": [],
            },
        ]
    )
    judge = FakeVllm([{"grounded": True, "unsupported_claims": []}])
    events = await run_pipeline(
        "Does travel insurance cover pre-existing conditions?", agent, fake_executor, judge
    )
    assert "Pre-Ex add-on" in texts(events)
    citations = [e.chunk_id for e in events if e.type == ChatEventType.citation]
    assert citations == ["tiq-trv/exclusions/pre-existing-conditions"]


async def test_uncited_answer_degrades_to_near_verbatim_block(fake_executor: FakeExecutor) -> None:
    agent = FakeVllm(
        [
            {"action": "tool", "tool": "search_kb", "args": {"query": "pre-existing conditions"}},
            {
                "action": "final",
                "final_text": "Everything is covered, no exclusions at all!",
                "citations": [],  # citation-presence grader must fail this
                "action_ids": [],
            },
            {  # retry also fails to cite
                "action": "final",
                "final_text": "Trust me, it is covered.",
                "citations": [],
                "action_ids": [],
            },
        ]
    )
    events = await run_pipeline("Are pre-existing conditions covered?", agent, fake_executor)
    assert "not covered" in texts(events)  # near-verbatim block text served instead
    assert any(e.type == ChatEventType.citation for e in events)


async def test_poisoned_web_chunk_is_screened_before_planner_sees_it(
    fake_executor: FakeExecutor,
) -> None:
    agent = FakeVllm(
        [
            {"action": "tool", "tool": "search_web_index", "args": {"query": "promotions"}},
            {
                "action": "final",
                "final_text": "There is a promotion running this month.",
                "citations": ["web:promo-1"],
                "action_ids": [],
            },
        ]
    )
    events = await run_pipeline("Any promotions now?", agent, fake_executor)
    # The second planner call contains the tool result; the imperative must be gone.
    tool_result_msg = "".join(m["content"] for m in agent.calls[1])
    assert "Ignore previous instructions" not in tool_result_msg
    assert "[removed-instruction]" in tool_result_msg
    assert events[-1].type == ChatEventType.done


async def test_servicing_guided_procedure_flow(fake_executor: FakeExecutor) -> None:
    agent = FakeVllm([{"intent": "update-address", "confidence": 0.95}])
    events = await run_pipeline("I want to update my address", agent, fake_executor)
    answer = texts(events)
    assert "customer portal" in answer.lower()
    assert CANNOT_EXECUTE_LINE in answer
    assert "3 working days" in answer
    citations = [e.chunk_id for e in events if e.type == ChatEventType.citation]
    assert citations == ["common/procedure/update-address"]
    actions = [e.action_id for e in events if e.type == ChatEventType.action]
    assert actions == ["customer-portal"]


async def test_low_confidence_classification_falls_through_to_agent_loop(
    fake_executor: FakeExecutor,
) -> None:
    agent = FakeVllm(
        [
            {"intent": "cancel-policy", "confidence": 0.3},  # below threshold
            {"action": "tool", "tool": "search_kb", "args": {"query": "cancel policy conditions"}},
            {
                "action": "final",
                "final_text": "I couldn't find the exact procedure — could you say which policy?",
                "citations": [],
                "action_ids": [],
                "is_clarification": True,
            },
        ]
    )
    events = await run_pipeline("Can I cancel something? Not sure what exactly", agent, fake_executor)
    assert events[-1].type == ChatEventType.done
    assert len(agent.calls) == 3  # classifier + 2 planner steps


async def test_escalation_emits_handover_payload(fake_executor: FakeExecutor) -> None:
    agent = FakeVllm(
        [
            {
                "action": "tool",
                "tool": "escalate_human",
                "args": {"reason": "customer dispute", "transcript_ref": "s1"},
            }
        ]
    )
    events = await run_pipeline(
        "I demand to speak to a manager about my rejected claim", agent, fake_executor
    )
    handovers = [e for e in events if e.type == ChatEventType.handover]
    assert len(handovers) == 1
    assert handovers[0].payload is not None
    assert handovers[0].payload["reason"] == "customer dispute"


async def test_no_agent_endpoint_serves_echo_stub(fake_executor: FakeExecutor) -> None:
    events = await run_pipeline("Does my plan cover golf equipment?", None, fake_executor)
    assert "still being set up" in texts(events)
    assert events[-1].type == ChatEventType.done
