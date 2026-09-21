"""Price workflow extracted from the serve pipeline."""

from __future__ import annotations

from harness import (
    AnswerEnvelope,
    Trace,
)
from harness.intent import Intent

from api.guidance import guidance
from api.handlers.contracts import Turn
from api.handlers.shared import _finish


def run(turn: Turn) -> tuple[AnswerEnvelope, Trace]:
    bundle = turn.bundle
    question = turn.question
    session = turn.session
    trace = turn.trace
    raw_root = turn.raw_root
    ask = turn.ask
    with trace.stage("route") as detail:
        detail["intent"] = Intent.price.value
        detail["product"] = ""
    return _finish(
        trace,
        guidance(bundle, raw_root, Intent.price, None, question),
        bundle,
        session,
        question,
        raw_root,
        [],
        ask=ask,
    )
