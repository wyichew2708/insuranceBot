import datetime as dt

from contracts.settings import Settings
from crawler.worker import build_page

NOW = dt.datetime(2026, 7, 31, 12, 0, tzinfo=dt.UTC)


def settings() -> Settings:
    return Settings(crawl_allowlist="www.tiq.com.sg,www.etiqa.com.sg")


def test_promo_page_gets_short_ttl_capped_by_validity() -> None:
    html = "<html><body><p>20% off! Promotion valid until 31 July 2026.</p></body></html>"
    page = build_page("https://www.tiq.com.sg/promotions/sale", html, "tiq", settings(), {}, now=NOW)
    assert page is not None
    assert page.page_type == "promo"
    assert page.expires_at.date() == dt.date(2026, 7, 31)


def test_default_page_gets_24h_ttl() -> None:
    html = "<html><body><p>Travel cover details.</p></body></html>"
    page = build_page("https://www.tiq.com.sg/travel-insurance", html, "tiq", settings(), {}, now=NOW)
    assert page is not None
    assert page.expires_at == NOW + dt.timedelta(hours=24)


def test_excluded_and_offlist_urls_skipped() -> None:
    s = settings()
    assert build_page("https://www.tiq.com.sg/buy-online/travel", "<p>x</p>", "tiq", s, {}, now=NOW) is None
    assert build_page("https://evil.example.com/promo", "<p>x</p>", "tiq", s, {}, now=NOW) is None
    assert (
        build_page("https://www.tiq.com.sg/policy-wordings/travel.pdf", "<p>x</p>", "tiq", s, {}, now=NOW)
        is None
    )


def test_boilerplate_fallback_strips_script_nav() -> None:
    html = "<html><nav>menu</nav><script>track()</script><p>Real content here</p></html>"
    page = build_page("https://www.tiq.com.sg/travel-insurance", html, "tiq", settings(), {}, now=NOW)
    assert page is not None
    assert "Real content" in page.text
    assert "track()" not in page.text and "menu" not in page.text
