"""Off topic workflow extracted from the serve pipeline."""

from __future__ import annotations

from harness import (
    AnswerEnvelope,
    GroundedAnswer,
    Trace,
)

from api.handlers.contracts import Turn
from api.handlers.shared import OFF_TOPIC, _finish


def run(turn: Turn) -> tuple[AnswerEnvelope, Trace]:
    bundle = turn.bundle
    question = turn.question
    session = turn.session
    trace = turn.trace
    raw_root = turn.raw_root
    ask = turn.ask
    with trace.stage("off-topic") as detail:
        detail["declined"] = True
    return _finish(
        trace,
        GroundedAnswer(answer=OFF_TOPIC, smalltalk=True, confidence=1.0),
        bundle,
        session,
        question,
        raw_root,
        [],
        ask=ask,
    )
