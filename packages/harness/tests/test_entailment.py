"""Groundedness reads meaning where a judge is present, and falls back where it is not."""

from __future__ import annotations

from typing import Any

from harness import Claim, GroundedAnswer, Session
from harness.gates import GateContext, gate_groundedness

from okf import Bundle

PAGE = "product/general/travel/exclusions"


def _ctx(bundle: Bundle, text: str, judge: Any) -> GateContext:
    answer = GroundedAnswer(answer=text, claims=[Claim(text=text, source_id=PAGE)])
    return GateContext(
        answer=answer,
        bundle=bundle,
        session=Session(session_id="t"),
        question="what is covered",
        loaded_page_ids=[PAGE],
        judge=judge,
    )


def test_a_contradiction_fails_even_when_every_word_is_in_the_source(bundle: Bundle) -> None:
    # Same tokens as the cancellation clause, opposite sense. Lexical overlap
    # would pass this; the judge must not.
    def judge(system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        assert "CLAIM 0" in user
        return {"verdicts": [{"claim": 0, "verdict": "contradicts"}]}

    result = gate_groundedness(_ctx(bundle, "Cover continues after the death of the insured person.", judge))
    assert result.blocking
    assert "contradicts" in result.detail


def test_neutral_on_a_load_bearing_claim_fails(bundle: Bundle) -> None:
    def judge(system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        return {"verdicts": [{"claim": 0, "verdict": "neutral"}]}

    # A money figure the judge will not vouch for is a hard fail; a bare
    # integer would defer to overlap instead.
    result = gate_groundedness(_ctx(bundle, "Cover pays up to S$30,000 for a claim.", judge))
    assert result.blocking
    assert "does not settle" in result.detail


def test_neutral_on_a_descriptive_claim_defers_to_overlap(bundle: Bundle) -> None:
    # No figure in the claim: "not settled" is the judge being careful, not a
    # missing number. The lexical test decides, and here the words are on the
    # page so it passes.
    def judge(system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        return {"verdicts": [{"claim": 0, "verdict": "neutral"}]}

    result = gate_groundedness(_ctx(bundle, "War, civil commotion and unlawful acts are excluded.", judge))
    assert not result.blocking
    assert "settled by overlap" in result.detail


def test_an_entailed_claim_passes(bundle: Bundle) -> None:
    def judge(system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        return {"verdicts": [{"claim": 0, "verdict": "entails"}]}

    result = gate_groundedness(_ctx(bundle, "Cover ends on the death of the insured person.", judge))
    assert not result.blocking
    assert "judged: 1 entailed" in result.detail


def test_a_silent_judge_falls_back_to_overlap_and_says_so(bundle: Bundle) -> None:
    def judge(system: str, user: str, schema: dict[str, Any]) -> None:
        return None

    result = gate_groundedness(_ctx(bundle, "Cover ends on the death of the insured person.", judge))
    assert "judge silent" in result.detail


def test_a_raising_judge_is_a_silent_judge(bundle: Bundle) -> None:
    def judge(system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("provider down")

    result = gate_groundedness(_ctx(bundle, "Cover ends on the death of the insured person.", judge))
    assert "judge silent" in result.detail


def test_no_judge_is_the_old_gate(bundle: Bundle) -> None:
    result = gate_groundedness(_ctx(bundle, "Cover ends on the death of the insured person.", None))
    assert "judge silent" not in result.detail
