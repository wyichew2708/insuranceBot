"""Advice workflow extracted from the serve pipeline."""

from __future__ import annotations

from harness import (
    AnswerEnvelope,
    Trace,
)

from api.guidance import adviser_referral
from api.handlers.contracts import Turn
from api.handlers.shared import _finish


def run(turn: Turn) -> tuple[AnswerEnvelope, Trace]:
    bundle = turn.bundle
    question = turn.question
    session = turn.session
    trace = turn.trace
    raw_root = turn.raw_root
    ask = turn.ask
    with trace.stage("advice") as detail:
        detail["referred"] = True
    return _finish(trace, adviser_referral(bundle), bundle, session, question, raw_root, [], ask=ask)
