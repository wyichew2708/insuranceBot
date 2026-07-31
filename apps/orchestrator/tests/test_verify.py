"""Verification loop: retry with feedback, judge integration, degrade path."""

from typing import Any

from conftest import FakeVllm
from orchestrator.verification import Draft, Evidence
from orchestrator.verify import (
    CLARIFY_FALLBACK,
    NEAR_VERBATIM_PREFIX,
    apply_answer_policies,
    verify_and_finalize,
)


def evidence() -> Evidence:
    return Evidence(
        cited_texts={"blk-1": "Call us on 6123 4567 for help."},
        cited_audiences={"blk-1": "public"},
        permitted_chunk_ids={"blk-1"},
        session_audience="public",
    )


async def test_clean_draft_passes_with_grounded_judge() -> None:
    judge = FakeVllm([{"grounded": True, "unsupported_claims": []}])
    draft = Draft(text="You can call us on 6123 4567.", citations=["blk-1"])
    outcome = await verify_and_finalize(draft, evidence(), [], judge=judge)
    assert outcome.passed and not outcome.degraded
    assert outcome.judge_verdict == {"grounded": True, "unsupported_claims": []}


async def test_grader_failure_retries_with_feedback_then_passes() -> None:
    feedback_seen: list[str] = []

    async def replan(feedback: str) -> Draft:
        feedback_seen.append(feedback)
        return Draft(text="You can call us on 6123 4567.", citations=["blk-1"])

    bad = Draft(text="You can call us on 6123 9999.", citations=["blk-1"])  # drifted digits
    outcome = await verify_and_finalize(bad, evidence(), [], replan=replan)
    assert outcome.passed and outcome.attempts == 2
    assert "verbatim-digits" in feedback_seen[0]


async def test_double_failure_degrades_to_near_verbatim_block() -> None:
    async def replan(feedback: str) -> Draft:
        return Draft(text="Still wrong: 6123 0000", citations=["blk-1"])

    bad = Draft(text="Call 6123 9999", citations=["blk-1"])
    outcome = await verify_and_finalize(bad, evidence(), [], replan=replan, max_retries=1)
    assert not outcome.passed and outcome.degraded
    assert outcome.draft.text.startswith(NEAR_VERBATIM_PREFIX)
    assert outcome.draft.citations == ["blk-1"]


async def test_ungrounded_judge_verdict_triggers_retry_then_degrade() -> None:
    judge = FakeVllm(
        [
            {"grounded": False, "unsupported_claims": ["made-up limit"]},
            {"grounded": False, "unsupported_claims": ["still made up"]},
        ]
    )

    async def replan(feedback: str) -> Draft:
        assert "made-up limit" in feedback
        return Draft(text="Another ungrounded claim.", citations=["blk-1"])

    draft = Draft(text="The limit is one million.", citations=["blk-1"])
    outcome = await verify_and_finalize(draft, evidence(), [], judge=judge, replan=replan)
    assert outcome.degraded


async def test_judge_down_means_rule_graders_only_not_failure() -> None:
    class DeadJudge:
        async def chat_structured(self, *a: Any, **kw: Any) -> dict[str, Any]:
            raise RuntimeError("endpoint down")

    draft = Draft(text="You can call us on 6123 4567.", citations=["blk-1"])
    outcome = await verify_and_finalize(draft, evidence(), [], judge=DeadJudge())
    assert outcome.passed
    assert outcome.judge_verdict is None


async def test_degrade_without_evidence_clarifies() -> None:
    bad = Draft(text="Some claim", citations=[])
    outcome = await verify_and_finalize(bad, Evidence(session_audience="public"), [])
    assert outcome.degraded
    assert outcome.draft.text == CLARIFY_FALLBACK


async def test_degrade_never_serves_internal_block_to_public() -> None:
    ev = Evidence(
        cited_texts={"int-1": "internal only text"},
        cited_audiences={"int-1": "internal"},
        permitted_chunk_ids={"int-1"},
        session_audience="public",
    )
    outcome = await verify_and_finalize(Draft(text="x", citations=[]), ev, [])
    assert outcome.draft.text == CLARIFY_FALLBACK


async def test_policies_attach_disclaimer_and_get_advice() -> None:
    tool_results = [
        {
            "tool": "search_kb",
            "args": {},
            "result": [{"chunk_id": "ben-1", "text": "benefit text", "metadata": {"type": "benefit"}}],
        }
    ]
    draft = Draft(text="I recommend the savings plan.", citations=["ben-1"])
    updated = apply_answer_policies(draft, tool_results)
    assert updated.text.count("[disclaimer]") == 1
    assert "[get-advice]" in updated.text
    assert "get-advice" in updated.action_ids
    assert updated.is_product_benefit_answer
