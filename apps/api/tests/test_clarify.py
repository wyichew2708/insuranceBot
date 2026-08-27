"""Asking which product was meant, instead of picking one.

`focus_product` picks the highest-scoring product and excludes every other one
from retrieval — which is how "cancer insurance" was answered from the
pet-insurance FAQ on an alphabetical tiebreak. The tiebreak is fixed; the shape
of the mistake is not. A system that must always choose will sometimes choose
wrong and say so with complete confidence.
"""

from __future__ import annotations

from api.clarify import MAX_OPTIONS, clarification
from harness import Verdict, run_gates

from conftest import make_session
from okf import Bundle


def test_two_plausible_products_produce_a_question(bundle: Bundle) -> None:
    asked = clarification(bundle, ["product/general/travel", "product/general/home"])
    assert asked is not None
    assert asked.clarifying
    assert "did you mean" in asked.answer.lower()
    assert "Travel Insurance" in asked.answer and "Home Insurance" in asked.answer


def test_one_product_is_not_a_choice(bundle: Bundle) -> None:
    assert clarification(bundle, ["product/general/travel"]) is None
    assert clarification(bundle, []) is None


def test_an_id_that_does_not_resolve_is_dropped(bundle: Bundle) -> None:
    assert clarification(bundle, ["product/general/travel", "product/general/nope"]) is None


def test_every_option_is_a_claim_on_the_page_it_came_from(bundle: Bundle) -> None:
    asked = clarification(bundle, ["product/general/travel", "product/general/home"])
    assert asked is not None
    for claim in asked.claims:
        assert bundle.get(claim.source_id) is not None
        assert claim.text in asked.answer


def test_the_question_is_not_a_menu(bundle: Bundle) -> None:
    ids = ["product/general/travel", "product/general/home", "product/motor/private-car"]
    asked = clarification(bundle, [*ids, *ids])
    assert asked is not None
    assert len(asked.claims) <= MAX_OPTIONS


def test_asking_passes_every_gate(bundle: Bundle) -> None:
    """It names products and asserts nothing about cover, so provenance
    applies and the coverage gates find nothing to check."""
    from harness import GateContext

    asked = clarification(bundle, ["product/general/travel", "product/general/home"])
    assert asked is not None
    results = run_gates(
        GateContext(
            answer=asked,
            bundle=bundle,
            session=make_session(),
            question="cover for my trip and my house",
            loaded_page_ids=[c.source_id for c in asked.claims],
            raw_root=bundle.root / "raw",
        )
    )
    assert not [r for r in results if r.verdict is Verdict.fail], results
