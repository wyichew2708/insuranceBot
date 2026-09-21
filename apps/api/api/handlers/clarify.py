"""Clarify workflow extracted from the serve pipeline."""

from __future__ import annotations

from harness import (
    AnswerEnvelope,
    Trace,
)

from api.clarify import (
    LISTABLE,
    MAX_OPTIONS,
    clarification,
    open_clarification,
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
    decision = turn.decision
    # A guess at a line lists the line: a customer who said "my flight was
    # delayed" is choosing among every travel plan, not the first three.
    limit = max(MAX_OPTIONS, min(len(decision.options), LISTABLE))
    asked = clarification(bundle, list(decision.options), limit=limit) if decision.options else None
    if asked is None:
        asked = open_clarification()
    with trace.stage("clarify") as detail:
        detail["layer2"] = decision.layer2.value
        detail["options"] = [c.source_id for c in asked.claims]
    return _finish(
        trace,
        asked,
        bundle,
        session,
        question,
        raw_root,
        [c.source_id for c in asked.claims],
        ask=ask,
    )
