"""Browse open workflow extracted from the serve pipeline."""

from __future__ import annotations

from harness import (
    AnswerEnvelope,
    Trace,
)

from api.directory import answer as directory_answer
from api.directory import lines_overview
from api.handlers.contracts import Turn
from api.handlers.shared import _finish
from api.retrieval import (
    unsupported_term,
)


def run(turn: Turn) -> tuple[AnswerEnvelope, Trace] | None:
    bundle = turn.bundle
    question = turn.question
    session = turn.session
    trace = turn.trace
    raw_root = turn.raw_root
    ask = turn.ask
    # A shopper who named a line this insurer does not write — "kidnap
    # and ransom cover" — is told so further down, never shown the
    # nearest thing we do sell (`api.directory.answer` says as much of
    # `None`, and on the real corpus "ransom" found Property Insurance).
    listed = None
    if not unsupported_term(bundle, question, []):
        listed = directory_answer(bundle, question) or lines_overview(bundle)
    if listed is not None:
        with trace.stage("directory") as detail:
            detail["listed"] = [c.source_id for c in listed.claims]
        return _finish(
            trace,
            listed,
            bundle,
            session,
            question,
            raw_root,
            [c.source_id for c in listed.claims],
            ask=ask,
        )
    return None
