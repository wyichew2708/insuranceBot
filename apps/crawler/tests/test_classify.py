import datetime as dt
from pathlib import Path

from crawler.classify import (
    canonicalize,
    classify_page,
    in_allowlist,
    is_demoted,
    is_excluded,
    is_record_only_pdf,
    load_canonical_map,
    parse_promo_validity,
)


def test_allowlist() -> None:
    allow = ["www.etiqa.com.sg", "www.tiq.com.sg"]
    assert in_allowlist("https://www.tiq.com.sg/travel", allow)
    assert not in_allowlist("https://aggregator.example.com/tiq-promo", allow)


def test_exclusions() -> None:
    assert is_excluded("https://www.tiq.com.sg/LoginPortal/home")
    assert is_excluded("https://www.tiq.com.sg/buy-online/travel")
    assert is_excluded("https://www.etiqa.com.sg/iConnect/dashboard")
    assert not is_excluded("https://www.tiq.com.sg/travel-insurance")


def test_pdf_policy_wordings_record_only() -> None:
    assert is_record_only_pdf("https://www.tiq.com.sg/policy-wordings/travel-v3.pdf")
    assert is_record_only_pdf("https://www.tiq.com.sg/files/brochure.pdf")
    assert not is_record_only_pdf("https://www.tiq.com.sg/travel-insurance")


def test_page_type_classification() -> None:
    cases = {
        "https://www.tiq.com.sg/travel-insurance/plans": "product",
        "https://www.tiq.com.sg/claims/how-to-claim": "claims",
        "https://www.etiqa.com.sg/policy-services/change-address": "servicing",
        "https://www.etiqa.com.sg/privacy-policy": "governance",
        "https://www.tiq.com.sg/promotions/august": "promo",
        "https://www.tiq.com.sg/blog/travel-tips": "blog",
        "https://www.tiq.com.sg/about-us": "other",
    }
    for url, expected in cases.items():
        assert classify_page(url) == expected, url


def test_canonical_map_rewrites_slug_drift(tmp_path: Path) -> None:
    map_file = tmp_path / "canonical_map.yml"
    map_file.write_text("/tiqinvest: /tiq-invest\n/faq/: /faqs/\n")
    cmap = load_canonical_map(map_file)
    assert canonicalize("https://www.tiq.com.sg/tiqinvest/", cmap) == "https://www.tiq.com.sg/tiq-invest"
    assert canonicalize("https://www.tiq.com.sg/faq/travel", cmap) == "https://www.tiq.com.sg/faqs/travel"
    assert load_canonical_map(tmp_path / "missing.yml") == {}


def test_promo_validity_parsing() -> None:
    text = "Save big! Information is accurate as of 1 July 2026. Promotion valid until 31 August 2026."
    accurate, until = parse_promo_validity(text)
    assert accurate == dt.date(2026, 7, 1)
    assert until == dt.date(2026, 8, 31)
    assert parse_promo_validity("no dates here") == (None, None)


def test_stale_demotion_signals() -> None:
    assert is_demoted("Travel advisory during COVID-19 period")
    assert is_demoted("This tranche is fully subscribed.")
    assert not is_demoted("Comprehensive travel cover for your holidays")
