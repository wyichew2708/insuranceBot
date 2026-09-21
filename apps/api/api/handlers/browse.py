"""Browse workflow extracted from the serve pipeline."""

from __future__ import annotations

from harness import (
    AnswerEnvelope,
    Trace,
)

from api.directory import answer as directory_answer
from api.handlers.contracts import Turn
from api.handlers.shared import _finish


def run(turn: Turn) -> tuple[AnswerEnvelope, Trace] | None:
    bundle = turn.bundle
    question = turn.question
    session = turn.session
    trace = turn.trace
    raw_root = turn.raw_root
    ask = turn.ask
    listing = directory_answer(bundle, question)
    if listing is not None:
        with trace.stage("directory") as detail:
            detail["products"] = [c.source_id for c in listing.claims]
        return _finish(
            trace,
            listing,
            bundle,
            session,
            question,
            raw_root,
            [c.source_id for c in listing.claims],
            ask=ask,
        )
    return None
