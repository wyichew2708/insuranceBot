"""Entity workflow extracted from the serve pipeline."""

from __future__ import annotations

from harness import (
    AnswerEnvelope,
    Trace,
)

from api.entity import answer as entity_answer
from api.handlers.contracts import Turn
from api.handlers.shared import _finish


def run(turn: Turn) -> tuple[AnswerEnvelope, Trace] | None:
    bundle = turn.bundle
    question = turn.question
    session = turn.session
    trace = turn.trace
    raw_root = turn.raw_root
    ask = turn.ask
    stated = entity_answer(bundle)
    if stated is not None:
        with trace.stage("entity") as detail:
            detail["underwriter"] = [c.text for c in stated.claims]
        return _finish(
            trace,
            stated,
            bundle,
            session,
            question,
            raw_root,
            [c.source_id for c in stated.claims],
            ask=ask,
        )
    return None
