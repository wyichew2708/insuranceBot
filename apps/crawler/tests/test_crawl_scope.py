"""Regression tests for crawl-scope defects found in review."""

from crawler.worker import _same_host, domain_brand, extract_links


def test_domain_brand_etiqa_not_misread_as_tiq() -> None:
    # "etiqa" contains the substring "tiq" — this used to misbrand every page.
    assert domain_brand("www.etiqa.com.sg") == "etiqa"
    assert domain_brand("www.tiq.com.sg") == "tiq"


def test_same_host_is_not_substring_matching() -> None:
    assert _same_host("https://www.tiq.com.sg/travel", "www.tiq.com.sg")
    assert _same_host("https://tiq.com.sg/travel", "www.tiq.com.sg")
    # domain appearing in path/query of another site must NOT count
    assert not _same_host("https://evil.example.com/?ref=www.tiq.com.sg", "www.tiq.com.sg")
    assert not _same_host("https://evil.example.com/www.tiq.com.sg/page", "www.tiq.com.sg")


def test_extract_links_keeps_fragment_links_as_page_urls() -> None:
    html = '<a href="/claims#how-to">claims</a> <a href="/travel">travel</a>'
    links = extract_links(html, "https://www.tiq.com.sg/")
    assert "https://www.tiq.com.sg/claims" in links
    assert "https://www.tiq.com.sg/travel" in links
