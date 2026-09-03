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
    assert named_products(real, "Early CI Protection Rider — Policy conditions — fraud") == [
        "early-ci-protection-rider"
    ]


def test_a_longer_title_absorbs_the_shorter_one_inside_it(real: Bundle) -> None:
    # "CI Benefit Rider" is a real product and also a suffix of this one. The
    # customer typed the long form; the short one is not a second candidate.
    assert named_products(real, "Early CI Benefit Rider free look period") == ["early-ci-benefit-rider"]


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


def test_the_longer_shopfront_name_names_the_add_on(real: Bundle) -> None:
    # "tiq travel" sits inside "tiq travel covid". The customer typed the
    # long form; the flagship is not a second candidate.
    assert named_products(real, "tiq travel covid coverage") == ["tiq-travel-covid"]


def test_a_product_named_outright_has_no_family_to_ask_about(real: Bundle) -> None:
    travel = real.get("product/general/travel-insurance")
    assert travel is not None
    assert product_family(real, "tiq travel coverage", travel) == []
    # A category is still a family: "travel insurance" alone is the title,
    # so it is named; "cancer insurance" is a phrase inside three titles.
    cancer = real.get("product/health-medical/cancer-insurance-with-no-claim-discount")
    if cancer is not None:
        assert len(product_family(real, "cancer insurance premium", cancer)) >= 2


def test_a_bare_name_is_a_request_for_the_overview(real: Bundle) -> None:
    travel = real.get("product/general/travel-insurance")
    assert travel is not None
    assert ask_about("tiq travel", travel).scope == "overview"
    assert ask_about("Tiq Travel Insurance please", travel).scope == "overview"
    assert ask_about("travel baggage per-item sub-limit", travel).scope == "specific"
    assert ask_about("tiq travel claim", travel).scope == "specific"
