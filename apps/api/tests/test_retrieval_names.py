"""A product named in full in the question is the product answered."""

from __future__ import annotations

import pytest
from api.retrieval import named_products

from okf import Bundle


@pytest.fixture(scope="module")
def real() -> Bundle:
    return Bundle.load("okf-real")


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
