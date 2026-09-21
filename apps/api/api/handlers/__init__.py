"""Inspectable workflows, selected in the pipeline's established precedence.

Layer-three intents share the retrieve/compose workflow. Their contracts are
registered separately so a tool-backed price or eligibility handler can replace
one without modifying the others. Registry-only paths cannot retrieve evidence.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from types import MappingProxyType

from harness import AnswerEnvelope, GroundedAnswer, Trace
from harness.contracts import GateResult, Verdict

from api.handlers import (
    account_state,
    advice,
    browse,
    browse_open,
    clarify,
    compare,
    domain,
    emergency,
    entity,
    knowledge,
    language,
    off_topic,
    price,
    smalltalk,
)
from api.handlers.contracts import Evidence, EvidenceViolation, Turn
from api.router import Layer3
from okf import PageType

Result = tuple[AnswerEnvelope, Trace]


@dataclass(frozen=True)
class Handler:
    kind: str
    evidence: frozenset[Evidence]
    run: Callable[[Turn], Result | None]
    gate_profile: frozenset[str] = frozenset()
    budget: str = "turn"  # the request's single shared Budget, never reset on fallback


CATALOGUE = frozenset({Evidence.catalogue, Evidence.registry})
KNOWLEDGE = frozenset(Evidence)
SOFT_GATES = frozenset({"numeric-binding", "answerability", "guardrail-output"})
_HANDLERS = {
    "language": Handler("language", frozenset({Evidence.registry}), language.run),
    "smalltalk": Handler("smalltalk", CATALOGUE, smalltalk.run),
    "domain": Handler("off_topic", CATALOGUE, domain.run),
    "advice": Handler("advice", CATALOGUE, advice.run),
    "entity": Handler("entity", CATALOGUE | {Evidence.wiki}, entity.run),
    "browse": Handler("browse", CATALOGUE, browse.run),
    "browse_open": Handler("browse", CATALOGUE, browse_open.run),
    "emergency": Handler("emergency", frozenset(), emergency.run),
    "off_topic": Handler("off_topic", frozenset(), off_topic.run),
    "account_state": Handler("account_state", CATALOGUE, account_state.run),
    "price": Handler("price", CATALOGUE, price.run),
    "clarify": Handler("clarify", CATALOGUE, clarify.run),
    **{
        f"knowledge.{kind.value}": Handler(kind.value, KNOWLEDGE, knowledge.run, SOFT_GATES)
        for kind in Layer3
        if kind is not Layer3.n_a
    },
    "knowledge.compare": Handler("compare", CATALOGUE | {Evidence.wiki}, compare.run),
}
REGISTRY = MappingProxyType(_HANDLERS)


def _validate(turn: Turn, result: Result) -> None:
    answer, trace = result[0].answer, result[1]
    if trace.loaded:
        turn.require(Evidence.wiki)
    if trace.rag_hits or trace.rag_used:
        turn.require(Evidence.raw)
    if trace.sor_calls or any(f.sor_field for f in answer.figures):
        turn.require(Evidence.sor)
    if any(f.page_ref or f.table_row_id for f in answer.figures):
        turn.require(Evidence.wiki)
    if any(f.quote_ref for f in answer.figures):
        turn.require(Evidence.raw)
    for claim in answer.claims:
        if claim.source_id.startswith("raw/"):
            turn.require(Evidence.raw)
        elif Evidence.wiki not in turn.evidence:
            # Catalogue paths may name plans; no policy clauses are evidence.
            turn.require(Evidence.catalogue)
            page = turn.bundle.get(claim.source_id)
            if page is None or page.frontmatter.type is not PageType.product or page.id.count("/") != 2:
                raise EvidenceViolation("catalogue workflow cited a non-product page")
            if claim.text not in {page.frontmatter.title, page.frontmatter.title.split(" — ")[0]}:
                raise EvidenceViolation("catalogue workflow asserted more than a product name")


def dispatch(name: str, turn: Turn) -> Result | None:
    handler = REGISTRY[name]
    turn.evidence, turn.soft_gates = handler.evidence, handler.gate_profile
    turn.trace.handler = name
    try:
        result = handler.run(turn)
        if result is not None:
            _validate(turn, result)
    except EvidenceViolation as exc:
        turn.trace.note(f"handler contract refused: {exc}")
        turn.trace.delivered = False
        failure = GateResult(gate="handler-evidence", verdict=Verdict.fail, detail=str(exc))
        turn.trace.gates.append(failure)
        answer = GroundedAnswer(
            answer="I can't establish that safely. Please contact our team for help.",
            handoff=True,
            confidence=0.0,
            unresolved=["handler evidence contract failed"],
        )
        result = (
            AnswerEnvelope(
                answer=answer, gates=turn.trace.gates, delivered=False, trace_id=turn.trace.trace_id
            ),
            turn.trace,
        )
        turn.trace.answer = answer.model_dump(mode="json")
    if result is not None:
        with turn.trace.stage("handler") as detail:
            detail.update(
                name=name,
                kind=handler.kind,
                evidence=sorted(e.value for e in handler.evidence),
                gate_profile=sorted(handler.gate_profile),
                budget=handler.budget,
            )
    return result


def dispatch_required(name: str, turn: Turn) -> Result:
    result = dispatch(name, turn)
    if result is None:
        raise RuntimeError(f"handler {name} did not produce its required answer")
    return result
