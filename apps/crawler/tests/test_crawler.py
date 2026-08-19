"""Crawler tests.

The whole synthetic site is served through an in-process MockTransport, so
these exercise the real discovery, politeness and extraction paths without a
single packet leaving the process.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import httpx
import pytest
from crawler.crawl import USER_AGENT, CrawlConfig, crawl, slugify_url
from crawler.extract import extract, parse_tables
from crawler.policy import Robots, canonical_url, classify, in_allowlist, is_excluded, is_record_only

from fixtures.synthetic_site import ETIQA, TIQ, build_site, transport

HOSTS = [ETIQA, TIQ]


def test_allowlist_is_host_equality_not_substring() -> None:
    assert in_allowlist("https://www.etiqa.example/personal/travel", [ETIQA])
    # The classic mistake: "www.etiqa.example.evil.com" contains the allowed host.
    assert not in_allowlist("https://www.etiqa.example.evil.test/x", [ETIQA])
    # www-insensitive, though: the apex and the www host are the same site.
    assert in_allowlist("https://etiqa.example/x", [ETIQA])
    assert not in_allowlist("https://www.example.test/x", [ETIQA])


def test_robots_longest_match_wins() -> None:
    robots = Robots.parse(
        "User-agent: *\nDisallow: /search\nAllow: /search/help\nCrawl-delay: 2\n"
        "Sitemap: https://h/sitemap.xml\n",
        USER_AGENT,
    )
    assert robots.allows("https://h/personal/travel")
    assert not robots.allows("https://h/search?q=x")
    assert robots.allows("https://h/search/help")
    assert robots.crawl_delay == 2
    assert robots.sitemaps == ["https://h/sitemap.xml"]


def test_canonical_url_strips_tracking_and_fragments() -> None:
    assert canonical_url("https://h/a/b/?utm_source=x#frag") == "https://h/a/b"
    assert canonical_url("https://h/a?page=2&gclid=z") == "https://h/a?page=2"


def test_classification_and_exclusions() -> None:
    assert classify("https://h/personal/travel") == "product"
    assert classify("https://h/claims/travel") == "claims"
    assert classify("https://h/faqs/travel") == "faq"
    assert classify("https://h/promotions") == "promo"
    assert is_excluded("https://h/wp-admin/edit.php")
    assert is_record_only("https://h/policy-wordings/travel-2026.pdf")


def test_extractor_drops_furniture_and_keeps_tables() -> None:
    site = build_site()
    html = site.pages[f"https://{ETIQA}/personal/travel/"]
    page = extract(html, f"https://{ETIQA}/personal/travel/")

    assert page.title == "Travel Insurance"
    assert page.canonical
    # Cookie banner, nav and footer are chrome, not content.
    for noise in ["Accept all cookies", "Copyright", "Skip to content"]:
        assert noise not in page.text
    # The <title> tag must not leak into the body.
    assert not page.text.lstrip().startswith("Travel Insurance |")
    assert page.tables and page.tables[0].header[0] == "Benefit"
    assert any("S$" in cell for row in page.tables[0].rows for cell in row)


def test_parse_tables_ignores_layout_tables() -> None:
    assert parse_tables("<table><tr><td>only</td></tr></table>") == []


def test_slugify_url_is_stable_and_bounded() -> None:
    assert slugify_url("https://h/personal/travel/") == "personal-travel"
    assert slugify_url("https://h/") == "index"
    assert len(slugify_url("https://h/" + "a" * 300)) <= 80


@pytest.mark.asyncio
async def test_full_crawl_writes_dated_snapshots(tmp_path: Path) -> None:
    config = CrawlConfig(
        allowlist=HOSTS,
        out_dir=tmp_path / "raw",
        requests_per_second=0,  # the rate limiter is exercised separately
        today=dt.date(2026, 8, 19),
    )
    async with httpx.AsyncClient(
        transport=transport(), headers={"User-Agent": USER_AGENT}, follow_redirects=True
    ) as client:
        result = await crawl(config, client)

    assert len(result.ok_pages) > 100
    assert set(result.hosts) == set(HOSTS)
    # Both brands were reached, and both wrote into their own dated directory.
    for host in HOSTS:
        directory = config.out_dir / "web" / host / "2026-08-19"
        assert directory.is_dir() and list(directory.glob("*.md"))

    # PDFs are recorded, never fetched as pages (§D.1).
    assert result.documents
    assert all(d["url"].endswith(".pdf") for d in result.documents)
    assert not any(p.url.endswith(".pdf") for p in result.pages)

    # Nothing outside the allowlist, nothing from an excluded path.
    for page in result.pages:
        assert page.host in HOSTS
        assert not is_excluded(page.url)

    snapshot = (config.out_dir / "web" / ETIQA / "2026-08-19" / "personal-travel.md").read_text()
    assert snapshot.startswith("---")
    assert '"product"' in snapshot and "content_hash" in snapshot
    assert "| Benefit |" in snapshot


@pytest.mark.asyncio
async def test_crawl_refuses_hosts_outside_the_allowlist(tmp_path: Path) -> None:
    config = CrawlConfig(allowlist=[ETIQA], out_dir=tmp_path / "raw", requests_per_second=0)
    async with httpx.AsyncClient(transport=transport(), follow_redirects=True) as client:
        result = await crawl(config, client)
    assert {p.host for p in result.pages} == {ETIQA}
