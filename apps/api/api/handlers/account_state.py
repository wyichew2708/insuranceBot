"""Account state workflow extracted from the serve pipeline."""

from __future__ import annotations

from harness import (
    AnswerEnvelope,
    Trace,
)
from harness.intent import Intent

from api.guidance import guidance
from api.handlers.contracts import Turn
from api.handlers.shared import _finish
from api.route import FRAUD_RE, destinations_for, fraud_opener


def run(turn: Turn) -> tuple[AnswerEnvelope, Trace]:
    bundle = turn.bundle
    question = turn.question
    session = turn.session
    trace = turn.trace
    raw_root = turn.raw_root
    ask = turn.ask
    routed_intent = ask.intent
    focus = bundle.get(ask.product_page) if ask.product_page else None
    with trace.stage("route") as detail:
        detail["intent"] = routed_intent.value
        detail["product"] = focus.id if focus is not None else ""
        detail["destinations"] = [d.url for d in destinations_for(routed_intent, focus, question)]
    # Not a refusal: the steps to the real answer, from the guidance
    # table (`api.guidance`). A fraud report keeps its safety line first
    # and goes to a person and nowhere else.
    fraud = routed_intent is Intent.contact and bool(FRAUD_RE.search(question))
    return _finish(
        trace,
        guidance(
            bundle,
            raw_root,
            routed_intent,
            focus,
            question,
            opener=fraud_opener(question) if fraud else None,
        ),
        bundle,
        session,
        question,
        raw_root,
        [],
        ask=ask,
    )
