"""Verification loop (§8.4): rule graders -> LLM judge -> 1 retry with grader
feedback -> degrade (closest block near-verbatim | clarify | escalate).

Nothing is streamed as final until this returns. Every verdict is recorded
via the auditor. Judge unavailability degrades to rule-graders-only mode
(Phase 5 failure drill), never to an unverified answer.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from orchestrator.evidence import benefit_answer
from orchestrator.planner import StructuredChat
from orchestrator.verification import (
    ADVICE_RE,
    DISCLAIMER_MARKER,
    GET_ADVICE_MARKER,
    Draft,
    Evidence,
    GraderResult,
    run_rule_graders,
    verdict,
)

logger = logging.getLogger("orchestrator.verify")

JUDGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "grounded": {"type": "boolean"},
        "unsupported_claims": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["grounded", "unsupported_claims"],
}

JUDGE_SYSTEM = (
    "You are a strict groundedness auditor. Every factual claim in the ANSWER "
    "must be supported by the SOURCES; numbers, dates and contact details must "
    "match exactly. A refusal or clarifying question is grounded by definition. "
    'Return {"grounded": bool, "unsupported_claims": [..]} only.'
)

CLARIFY_FALLBACK = (
    "I couldn't verify a reliable answer to that. Could you rephrase the "
    "question, or tell me which product you're asking about?"
)

NEAR_VERBATIM_PREFIX = "Here is what our official information says:\n\n"

Replan = Callable[[str], Awaitable[Draft | None]]


@dataclass
class VerifyOutcome:
    draft: Draft
    passed: bool
    degraded: bool = False
    grader_results: list[GraderResult] = field(default_factory=list)
    judge_verdict: dict[str, Any] | None = None
    attempts: int = 1


def apply_answer_policies(draft: Draft, tool_results: list[dict[str, Any]]) -> Draft:
    """Mechanical post-processing that encodes product rules 6/7 before grading:
    attach the disclaimer marker to benefit answers, attach Get Advice routing
    to advice-like phrasing. Markers are expanded by the renderer."""
    text = draft.text
    is_benefit = benefit_answer(tool_results, draft.citations)
    if is_benefit and DISCLAIMER_MARKER not in text:
        text = f"{text}\n\n{DISCLAIMER_MARKER}"
    action_ids = list(draft.action_ids)
    if ADVICE_RE.search(text) and GET_ADVICE_MARKER not in text:
        text = f"{text}\n\n{GET_ADVICE_MARKER}"
        if "get-advice" not in action_ids:
            action_ids.append("get-advice")
    return draft.model_copy(
        update={"text": text, "action_ids": action_ids, "is_product_benefit_answer": is_benefit}
    )


async def judge_groundedness(
    judge: StructuredChat, draft: Draft, ev: Evidence, trace_id: str = ""
) -> dict[str, Any] | None:
    """Returns the verdict, or None when the judge is unreachable (rule-graders-only mode)."""
    sources = "\n\n".join(f"[{cid}]\n{text}" for cid, text in ev.cited_texts.items())
    try:
        return await judge.chat_structured(
            [
                {"role": "system", "content": JUDGE_SYSTEM},
                {"role": "user", "content": f"SOURCES:\n{sources}\n\nANSWER:\n{draft.text}"},
            ],
            JUDGE_SCHEMA,
            trace_id=trace_id,
        )
    except Exception as exc:
        logger.warning("judge unavailable, rule-graders-only mode: %s", exc)
        return None


def degrade(ev: Evidence) -> Draft:
    """Serve the closest permitted block near-verbatim with citation, else clarify."""
    for chunk_id, text in ev.cited_texts.items():
        if ev.cited_audiences.get(chunk_id) == "internal" and ev.session_audience != "internal":
            continue
        if text.strip():
            return Draft(
                text=NEAR_VERBATIM_PREFIX + text.strip(),
                citations=[chunk_id],
                is_factual=True,
            )
    return Draft(text=CLARIFY_FALLBACK, is_factual=False)


async def verify_and_finalize(
    draft: Draft,
    ev: Evidence,
    tool_results: list[dict[str, Any]],
    judge: StructuredChat | None = None,
    replan: Replan | None = None,
    max_retries: int = 1,
    use_judge: bool = True,
    audit: Callable[[str, dict[str, Any]], None] | None = None,
    trace_id: str = "",
) -> VerifyOutcome:
    attempts = 0
    current = apply_answer_policies(draft, tool_results)
    last_results: list[GraderResult] = []
    judge_result: dict[str, Any] | None = None

    while True:
        attempts += 1
        last_results = run_rule_graders(current, ev)
        rules_ok = verdict(last_results)
        judge_result = None
        judge_ok = True
        if rules_ok and use_judge and judge is not None and current.is_factual:
            judge_result = await judge_groundedness(judge, current, ev, trace_id)
            if judge_result is not None:
                judge_ok = bool(judge_result.get("grounded"))

        if audit:
            audit(
                "verification_verdict",
                {
                    "attempt": attempts,
                    "rules": [{"name": r.name, "passed": r.passed, "reason": r.reason} for r in last_results],
                    "judge": judge_result,
                },
            )

        if rules_ok and judge_ok:
            return VerifyOutcome(
                draft=current,
                passed=True,
                grader_results=last_results,
                judge_verdict=judge_result,
                attempts=attempts,
            )

        if attempts <= max_retries and replan is not None:
            feedback_parts = [f"{r.name}: {r.reason}" for r in last_results if not r.passed]
            if judge_result is not None and not judge_ok:
                claims = judge_result.get("unsupported_claims") or []
                feedback_parts.append("unsupported claims: " + "; ".join(map(str, claims)))
            revised = await replan("Your previous draft failed verification: " + " | ".join(feedback_parts))
            if revised is not None:
                current = apply_answer_policies(revised, tool_results)
                continue

        degraded = degrade(ev)
        return VerifyOutcome(
            draft=degraded,
            passed=False,
            degraded=True,
            grader_results=last_results,
            judge_verdict=judge_result,
            attempts=attempts,
        )
