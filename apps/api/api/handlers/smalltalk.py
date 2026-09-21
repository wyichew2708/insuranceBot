"""Smalltalk workflow extracted from the serve pipeline."""

from __future__ import annotations

from harness import (
    AnswerEnvelope,
    GroundedAnswer,
    Trace,
)
from harness.intent import smalltalk_kind

from api.handlers.contracts import Turn
from api.handlers.shared import _finish, _pleasantry


def run(turn: Turn) -> tuple[AnswerEnvelope, Trace]:
    bundle = turn.bundle
    question = turn.question
    session = turn.session
    trace = turn.trace
    raw_root = turn.raw_root
    ask = turn.ask
    kind = smalltalk_kind(question)
    assert kind is not None
    with trace.stage("smalltalk") as detail:
        detail["kind"] = kind
    answer = GroundedAnswer(
        answer=_pleasantry(kind, bundle),
        smalltalk=True,
        confidence=1.0,
    )
    # The gates still run. Every one of them will skip — there is nothing
    # to check in a reply that asserts nothing — but a turn that silently
    # bypassed verification would be indistinguishable, on the trace, from
    # one that passed it. Skipping on the record is the point.
    return _finish(trace, answer, bundle, session, question, raw_root, [], ask=ask)
