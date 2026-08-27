"""Reading which product a question is about, instead of counting words.

The safety property is that this can only improve selection. Every failure
path — no model, a timeout, malformed output, an id that was never offered —
returns nothing and the lexical path runs exactly as it does today.
"""

from __future__ import annotations

from typing import Any

from api.understand import SHORTLIST, understand, worth_resolving

from okf import Bundle


class _Provider:
    """A provider whose verdict the test dictates."""

    name = "stub"

    def __init__(self, payload: Any) -> None:
        self.payload = payload
        self.seen: dict[str, Any] = {}

    def classify(self, system: str, user: str, schema: dict[str, Any], **kw: Any) -> Any:
        self.seen = {"system": system, "user": user, "schema": schema}
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


def test_a_resolved_id_is_used(bundle: Bundle) -> None:
    provider = _Provider({"product_ids": ["product/general/travel"], "ambiguous": False})
    out = understand(bundle, "want to buy trip cover", provider)
    assert out.resolved
    assert out.product_ids == ["product/general/travel"]


def test_an_id_that_was_never_offered_is_discarded(bundle: Bundle) -> None:
    """A model answering with a product it was not shown has not selected, it
    has recalled — and recall is the failure this design exists to exclude."""
    provider = _Provider({"product_ids": ["product/general/not-a-real-product"], "ambiguous": False})
    out = understand(bundle, "anything", provider)
    assert not out.resolved
    assert out.degraded == "no id resolved"


def test_a_provider_fault_is_a_degraded_turn_not_a_failed_one(bundle: Bundle) -> None:
    out = understand(bundle, "anything", _Provider(TimeoutError("slow")))
    assert not out.resolved
    assert out.degraded == "TimeoutError"


def test_malformed_output_falls_through(bundle: Bundle) -> None:
    assert not understand(bundle, "anything", _Provider("not a dict")).resolved
    assert not understand(bundle, "anything", _Provider(None)).resolved


def test_the_deterministic_provider_is_never_asked(bundle: Bundle) -> None:
    """CI and the eval suites run offline and free, and measure the retrieval
    everyone else gets."""
    from api.llm import DeterministicProvider

    out = understand(bundle, "want to buy trip cover", DeterministicProvider())
    assert out.degraded == "no model"


def test_the_model_only_ever_sees_ids_that_exist(bundle: Bundle) -> None:
    provider = _Provider({"product_ids": [], "ambiguous": False})
    understand(bundle, "travel", provider)
    offered = [
        line.split(" — ")[0].removeprefix("- ")
        for line in provider.seen["user"].splitlines()
        if line.startswith("- ")
    ]
    assert offered
    assert len(offered) <= SHORTLIST
    assert all(bundle.get(page_id) is not None for page_id in offered)


def test_ambiguity_is_reported_rather_than_resolved(bundle: Bundle) -> None:
    """A customer asked which one they meant is better served than one given a
    guess — the caller decides, and today it declines to pin a focus."""
    provider = _Provider(
        {"product_ids": ["product/general/travel", "product/general/home"], "ambiguous": True}
    )
    out = understand(bundle, "cover for my trip and my house", provider)
    assert out.ambiguous
    assert len(out.product_ids) == 2


def test_an_empty_turn_is_not_worth_resolving() -> None:
    assert not worth_resolving("")
    assert not worth_resolving("???")
    assert worth_resolving("travel")
