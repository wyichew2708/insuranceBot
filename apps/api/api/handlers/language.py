"""A source-language gap is an explicit localised handoff, never a translation."""

from harness import AnswerEnvelope, GroundedAnswer, Trace
from harness.contracts import Link

from api.handlers.contracts import Evidence, Turn
from api.handlers.shared import CONTACT_LINK, _fail_closed, _finish, _refusal
from api.language import CONTACT_LABEL, FALLBACK, detect_language


def run(turn: Turn) -> tuple[AnswerEnvelope, Trace]:
    turn.require(Evidence.registry)
    language = detect_language(turn.question)
    answer = GroundedAnswer(
        answer=FALLBACK[language],
        handoff=True,
        confidence=0.0,
        destinations=[Link(label=CONTACT_LABEL[language], url=CONTACT_LINK.url, desk=CONTACT_LINK.desk)],
    )
    outgoing = turn.guard.screen_output(
        turn.question, "Approved corpus language: English only.", answer.answer, []
    )
    if outgoing.blocked or _fail_closed(outgoing, turn.settings):
        return _refusal(turn.trace, outgoing, "guardrail-output", "language handoff failed screening")
    envelope, trace = _finish(
        turn.trace,
        answer,
        turn.bundle,
        turn.session,
        turn.question,
        turn.raw_root,
        [],
        ask=turn.ask,
    )
    envelope.gates.append(outgoing.as_gate("guardrail-output"))
    trace.gates = envelope.gates
    return envelope, trace
