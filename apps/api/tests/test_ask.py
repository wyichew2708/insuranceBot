"""One reading of the question, and what each field is set from."""

from __future__ import annotations

from pathlib import Path

import pytest
from harness.ask import read_ask
from harness.intent import Intent

from okf import Bundle


@pytest.fixture(scope="module")
def real() -> Bundle:
    return Bundle.load(Path("okf-real"))


def test_the_shopfront_name_is_the_customers_answer_to_which_product(real: Bundle) -> None:
    ask = read_ask(real, "tiq travel coverage")
    assert ask.product == "travel-insurance"
    assert ask.named and ask.named_by == "alias"
    assert ask.intent is Intent.coverage
    assert ask.scope == "overview"
    assert not ask.ambiguous


def test_a_bare_name_asks_what_the_product_is(real: Bundle) -> None:
    ask = read_ask(real, "tiq travel")
    assert ask.product == "travel-insurance"
    assert ask.scope == "overview"
    assert ask.evidence["scope"] == "bare name"


def test_a_benefit_named_is_not_an_overview(real: Bundle) -> None:
    ask = read_ask(real, "travel insurance baggage per-item sub-limit")
    assert ask.product == "travel-insurance"
    assert ask.scope == "specific"


def test_the_longer_name_names_the_add_on(real: Bundle) -> None:
    assert read_ask(real, "tiq travel covid coverage").product == "tiq-travel-covid"


def test_a_category_with_no_flagship_is_a_family_to_ask_about(real: Bundle) -> None:
    ask = read_ask(real, "cancer insurance premium")
    assert ask.product is None
    assert ask.ambiguous and len(ask.family) >= 2


def test_a_procedure_keeps_its_intent_and_its_product(real: Bundle) -> None:
    ask = read_ask(real, "how do I buy travel insurance")
    assert ask.product == "travel-insurance"
    assert ask.intent is Intent.application
    assert ask.scope == "specific"


def test_nothing_named_is_left_open_for_the_model(real: Bundle) -> None:
    ask = read_ask(real, "my place was broken into, am I covered")
    assert ask.product is None and not ask.family
    assert ask.named is False


def test_the_model_never_overrules_a_named_product(real: Bundle) -> None:
    ask = read_ask(real, "tiq travel coverage")
    filled = ask.with_model(["product/general/tiq-travel-covid"], False, "product", real)
    assert filled.product == "travel-insurance"
