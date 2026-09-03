"""The product page and the documents may support an answer; marketing may not."""

from __future__ import annotations

from okf.sources import DOCUMENT, MARKETING, OFFER, PRODUCT_PAGE, UNKNOWN, may_support, source_class


def test_documents_are_supporting_whatever_the_question() -> None:
    assert source_class("raw/wordings/tiq-home-policy-wording-v9.md#p3") == DOCUMENT
    assert source_class("raw/faq/home-insurance.md") == DOCUMENT
    assert may_support("raw/product-summaries/x.md", "what does it cover")


def test_a_crawled_page_is_classified_by_its_page_type() -> None:
    ref = "raw/web/www.tiq.com.sg/2026-08-25/product-home-insurance.md#why"
    assert source_class(ref, page_type="product") == PRODUCT_PAGE
    assert source_class(ref, page_type="claims") == PRODUCT_PAGE
    assert source_class(ref, page_type="blog") == MARKETING
    assert source_class(ref, page_type="other") == MARKETING
    assert source_class(ref, page_type="promo") == OFFER


def test_marketing_never_supports_and_an_offer_only_when_asked() -> None:
    blog = "raw/web/www.tiq.com.sg/2026-08-25/blog-choose-right-travel-insurance.md"
    promo = "raw/web/www.tiq.com.sg/2026-08-25/promotion-tiq-home-insurance-promo.md"
    assert not may_support(blog, "what does travel insurance cover", page_type="blog")
    assert not may_support(promo, "what does home insurance cover", page_type="promo")
    assert may_support(promo, "is there a promotion for home insurance", page_type="promo")


def test_a_wiki_page_id_is_not_a_raw_source() -> None:
    assert source_class("product/general/home-insurance#What it covers") == UNKNOWN
