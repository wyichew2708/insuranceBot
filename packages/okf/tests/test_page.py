import datetime as dt

import pytest

from okf import Status, parse_page, render_page

DOC = """---
okf_version: "0.1"
id: product/general/travel
title: Travel Insurance
type: product
status: approved
lifecycle: on_sale
aliases: ["Tiq Travel", "ePROTECT travel"]
jurisdiction: SG
version_in_force: "2026.1"
effective_from: 2026-01-01
review_due: 2026-11-18
links:
  benefits: product/general/travel/benefits
  concepts: [concept/free-look]
---

## Body

Some prose [src:raw/wordings/travel-2026.1.md#s4.2].
"""


def test_roundtrip_preserves_frontmatter_and_body() -> None:
    page = parse_page(DOC)
    reparsed = parse_page(render_page(page))
    assert reparsed.frontmatter == page.frontmatter
    assert reparsed.body.strip() == page.body.strip()


def test_effective_window() -> None:
    fm = parse_page(DOC).frontmatter
    assert fm.is_effective_on(dt.date(2026, 6, 1))
    assert not fm.is_effective_on(dt.date(2025, 6, 1))


def test_review_overdue_demotes_page() -> None:
    fm = parse_page(DOC).frontmatter
    assert not fm.is_review_overdue(dt.date(2026, 8, 19))
    assert fm.is_review_overdue(dt.date(2026, 12, 1))


def test_links_collects_every_edge() -> None:
    fm = parse_page(DOC).frontmatter
    assert set(fm.links.all_refs()) == {"product/general/travel/benefits", "concept/free-look"}


def test_id_must_be_a_slug_path() -> None:
    with pytest.raises(ValueError):
        parse_page(DOC.replace("id: product/general/travel", "id: Product/General/Travel"))


def test_status_enum() -> None:
    assert parse_page(DOC).frontmatter.status is Status.approved


def test_missing_fence_is_rejected() -> None:
    with pytest.raises(ValueError):
        parse_page("# no frontmatter")


def test_section_lookup() -> None:
    assert "Some prose" in (parse_page(DOC).section("Body") or "")
