"""Emergency workflow extracted from the serve pipeline."""

from __future__ import annotations

from harness import (
    AnswerEnvelope,
    GroundedAnswer,
    Trace,
)

from api.guardrails import (
    MEDICAL_EMERGENCY,
)
from api.handlers.contracts import Turn
from api.handlers.shared import _finish


def run(turn: Turn) -> tuple[AnswerEnvelope, Trace]:
    bundle = turn.bundle
    question = turn.question
    session = turn.session
    trace = turn.trace
    raw_root = turn.raw_root
    ask = turn.ask
    with trace.stage("medical-emergency") as detail:
        detail["routed"] = "care"
    return _finish(
        trace,
        GroundedAnswer(answer=MEDICAL_EMERGENCY, smalltalk=True, handoff=True, confidence=1.0),
        bundle,
        session,
        question,
        raw_root,
        [],
        ask=ask,
    )
