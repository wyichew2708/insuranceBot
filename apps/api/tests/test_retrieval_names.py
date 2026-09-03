"""A product named in full in the question is the product answered."""

from __future__ import annotations

from pathlib import Path

import pytest
from api.retrieval import named_products, product_family
from harness.ask import ask_about

from okf import Bundle


@pytest.fixture(scope="module")
def real() -> Bundle:
    return Bundle.load(Path("okf-real"))


def test_full_title_names_the_product(real: Bundle) -> None:
    assert named_products(real, "Tiq 3 Plus Critical Illness — Policy conditions — fraud") == [
        "3-plus-critical-illness"
    ]


def test_a_longer_title_absorbs_the_shorter_one_inside_it(real: Bundle) -> None:
    # "Invest vista" is a product and also sits inside "Invest Smart Vista".
    # The customer typed the long form; the short one is not a second candidate.
    assert named_products(real, "Invest Smart Vista free look period") == ["invest-smart-vista"]


def test_no_title_means_no_override(real: Bundle) -> None:
    assert named_products(real, "how do i make a claim") == []


def test_one_word_titles_are_vocabulary_not_identity(real: Bundle) -> None:
    # "Life" and "Travel" are product titles in this bundle. A single word is
    # not a customer naming a product.
    assert "life" not in named_products(real, "what does life mean in the wording")


def test_the_shopfront_name_names_the_flagship(real: Bundle) -> None:
    # The wiki title is "Travel Insurance"; the site the customer bought from
    # calls it "Tiq Travel Insurance". Both are its name.
    assert named_products(real, "tiq travel coverage") == ["travel-insurance"]
    assert named_products(real, "Tiq Travel Insurance coverage") == ["travel-insurance"]


def test_a_longer_phrase_around_a_name_still_names_it(real: Bundle) -> None:
    # The Covid add-on is not a catalogue product, so "tiq travel covid"
    # names Tiq Travel Insurance and nothing else.
    assert named_products(real, "tiq travel covid coverage") == ["travel-insurance"]


def test_a_product_named_outright_has_no_family_to_ask_about(real: Bundle) -> None:
    travel = real.get("product/general/travel-insurance")
    assert travel is not None
    assert product_family(real, "tiq travel coverage", travel) == []
    # A category is still a family: "direct etiqa" sits inside two titles.
    term = real.get("product/protection/direct-etiqa-term-life-ii")
    assert term is not None
    assert len(product_family(real, "direct etiqa premium", term)) >= 2


def test_a_bare_name_is_a_request_for_the_overview(real: Bundle) -> None:
    travel = real.get("product/general/travel-insurance")
    assert travel is not None
    assert ask_about("tiq travel", travel).scope == "overview"
    assert ask_about("Tiq Travel Insurance please", travel).scope == "overview"
    assert ask_about("travel baggage per-item sub-limit", travel).scope == "specific"
    assert ask_about("tiq travel claim", travel).scope == "specific"
