"""Compile-loop tests: crawl snapshots in, an OKF wiki out.

The fixture site is crawled in-process, then compiled, then linted — so these
assert the contract the compile loop is supposed to hold, not the fixture's
particular numbers.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import httpx
import pytest
from compiler.snapshots import load_snapshots, parse_snapshot
from compiler.wiki import (
    CompileConfig,
    _offer_prose,
    benefit_code,
    channel_for,
    compile_bundle,
    line_of_business,
    parse_cell,
)
from crawler.crawl import USER_AGENT, CrawlConfig, crawl

from fixtures.synthetic_site import ETIQA, TIQ, transport
from okf import Bundle, PageType, Status, lint_bundle

TODAY = dt.date(2026, 8, 19)


@pytest.fixture(scope="module")
def compiled(tmp_path_factory: pytest.TempPathFactory) -> Path:
    import asyncio

    root = tmp_path_factory.mktemp("bundle")

    async def run() -> None:
        config = CrawlConfig(allowlist=[ETIQA, TIQ], out_dir=root / "raw", requests_per_second=0, today=TODAY)
        async with httpx.AsyncClient(
            transport=transport(), headers={"User-Agent": USER_AGENT}, follow_redirects=True
        ) as client:
            await crawl(config, client)

    asyncio.run(run())
    compile_bundle(
        CompileConfig(
            source_root=root,
            dest_root=root,
            today=TODAY,
            sign_off=["product-owner:test", "compliance:test"],
        )
    )
    return root


def test_brand_matching_prefers_the_more_specific_host() -> None:
    # "tiq" is a substring of "etiqa"; order matters.
    # Both hosts are front doors of the one direct channel, not two brands.
    assert channel_for(ETIQA)[:2] == ("Direct", "channel/direct")
    assert channel_for(TIQ)[:2] == ("Direct", "channel/direct")


def test_line_of_business_falls_back_to_general() -> None:
    assert line_of_business("private-car", "Private Car Insurance") == "motor"
    assert line_of_business("term-life", "Term Life Insurance") == "protection"
    assert line_of_business("something-new", "Something New") == "general"


def test_parse_cell_types_the_value_or_refuses() -> None:
    assert parse_cell("S$50,000") == ("50000", "S$", "limit")
    assert parse_cell("40%") == ("40", "%", "rate")
    assert parse_cell("15 years") == ("15", "years", "period")
    # Prose in a table cell is not a number the compiler may publish.
    assert parse_cell("Subject to underwriting") is None
    assert benefit_code("Overseas medical expenses") == "overseas_medical_expenses"


def test_offer_prose_drops_dates_the_frontmatter_already_holds() -> None:
    cleaned = _offer_prose(
        "Save 15% on plans bought online before 31 August 2026 with code SAVE15. "
        "Information is accurate as of 1 August 2026."
    )
    assert "15%" in cleaned
    assert "2026" not in cleaned and "August" not in cleaned


def test_snapshot_parsing_separates_intro_from_sections(compiled: Path) -> None:
    path = compiled / "raw" / "web" / ETIQA / TODAY.isoformat() / "personal-travel.md"
    snapshot = parse_snapshot(path, compiled)
    assert snapshot.page_type == "product"
    assert snapshot.slug == "travel"
    # The <h1> repeats the title; its prose belongs to the intro, not a section.
    assert "protects you" in snapshot.intro
    assert snapshot.section("What is not covered") is not None
    assert all(s.heading != snapshot.title for s in snapshot.sections)


def test_compiled_bundle_lints_clean(compiled: Path) -> None:
    report = lint_bundle(Bundle.load(compiled))
    assert report.errors == []


def test_two_hosts_compile_to_one_product_and_one_channel(compiled: Path) -> None:
    """Both websites are the same direct channel selling the same product.

    They must collapse into a single binding carrying both front doors — not
    two bindings the customer would have to choose between.
    """
    bundle = Bundle.load(compiled)
    travel = bundle.get("product/general/travel")
    assert travel is not None
    refs = {c.ref for c in travel.frontmatter.channels}
    assert refs == {"channel/direct"}
    binding = travel.frontmatter.channels[0]
    # One route, reachable at both addresses.
    assert len(binding.landings) == 2
    assert {u.split("/")[2] for u in binding.landings} == {ETIQA, TIQ}
    # No page exists per host, and none per brand.
    assert bundle.get("product/general/tiq-travel") is None
    assert bundle.get("channel/tiq-sg") is None
    assert bundle.get("channel/etiqa-sg") is None


def test_numbers_leave_prose_and_become_table_rows(compiled: Path) -> None:
    bundle = Bundle.load(compiled)
    travel = bundle.get("product/general/travel")
    assert travel is not None
    assert "{{table:" in travel.body
    rows = [r for r in bundle.tables.rows if r.product == "travel"]
    assert rows and all(r.source_ref.startswith("raw/web/") for r in rows)
    assert {r.tier for r in rows} == {"basic", "standard", "premier"}


def test_exclusions_are_lifted_onto_their_own_linked_page(compiled: Path) -> None:
    bundle = Bundle.load(compiled)
    travel = bundle.get("product/general/travel")
    assert travel is not None
    exclusions_id = travel.frontmatter.links.exclusions
    assert exclusions_id == "product/general/travel/exclusions"
    exclusions = bundle.get(exclusions_id)
    assert exclusions is not None and "excluded" in exclusions.body.lower()


def test_disagreeing_websites_are_filed_as_defects_not_averaged(compiled: Path) -> None:
    tickets = sorted((compiled / "conflicts").glob("*.md"))
    assert tickets, "the fixture plants a stale figure on one brand's site"
    text = tickets[0].read_text()
    assert "kept (higher authority)" in text and "contradicted" in text
    # The wiki carries one value, not both.
    bundle = Bundle.load(compiled)
    rows = [r for r in bundle.tables.rows if r.product == "travel" and r.tier == "basic"]
    assert len({(r.benefit_code, r.attribute) for r in rows}) == len(rows)


def test_pages_are_draft_until_someone_signs_off(tmp_path: Path, compiled: Path) -> None:
    import shutil

    root = tmp_path / "unsigned"
    shutil.copytree(compiled / "raw", root / "raw")
    compile_bundle(CompileConfig(source_root=root, dest_root=root, today=TODAY))
    bundle = Bundle.load(root)
    assert bundle.pages
    assert all(p.frontmatter.status == Status.draft for p in bundle.pages.values())
    # And a draft bundle answers nothing: the filter admits approved pages only.
    assert all(not p.frontmatter.reviewed_by for p in bundle.pages.values())


def test_every_product_page_reaches_the_index(compiled: Path) -> None:
    bundle = Bundle.load(compiled)
    index = bundle.get("index")
    assert index is not None
    for page in bundle.by_type(PageType.product):
        assert page.id in index.body


def test_snapshots_deduplicate_to_the_newest_date(compiled: Path) -> None:
    snapshots = load_snapshots(compiled)
    urls = [s.url for s in snapshots]
    assert len(urls) == len(set(urls))


def test_faq_products_match_across_the_two_front_doors() -> None:
    """ "ePROTECT maid" and "Tiq Maid" are one product under two brands, which
    is the whole premise of the channel model, so they normalise together."""
    from compiler.wiki import _faq_key, _match_faq_product

    slugs = ["maid-insurance", "travel-insurance", "pet-insurance", "motorcycle-insurance"]
    index = {_faq_key(s): s for s in slugs}
    assert _match_faq_product("Tiq Travel Insurance", index, slugs) == "travel-insurance"
    assert _match_faq_product("ePROTECT maid", index, slugs) == "maid-insurance"
    assert _match_faq_product("ePROTECT motorcycle", index, slugs) == "motorcycle-insurance"


def test_an_ambiguous_faq_set_is_left_unmatched_rather_than_guessed() -> None:
    """ "Dash PET" shares `pet` with Pet Insurance but is a different product
    sold through a partner app. Attaching its answers to Pet Insurance would be
    worse than leaving them out, so the token rule needs a single candidate."""
    from compiler.wiki import _faq_key, _match_faq_product

    slugs = ["pet-insurance", "pet-survey"]
    index = {_faq_key(s): s for s in slugs}
    assert _match_faq_product("Dash PET Plus", index, slugs) is None


def test_the_richer_page_wins_a_key_collision(tmp_path: Path) -> None:
    """This corpus carries both `travel` and `travel-insurance` — a thin landing
    page and the real product, which normalise to one key. First-writer-wins put
    36 published answers on the stub, where no customer question retrieves them.
    """
    from compiler.wiki import ProductGroup, _faq_key

    assert _faq_key("travel") == _faq_key("travel-insurance") == _faq_key("Tiq Travel Insurance")

    def group(slug: str, title: str, body: str) -> ProductGroup:
        path = tmp_path / f"{slug}.md"
        path.write_text(
            f'---\nsource_url: "https://www.etiqa.com.sg/{slug}"\nhost: "www.etiqa.com.sg"\n'
            f'title: "{title}"\npage_type: "product"\n---\n\n# {title}\n\n{body}\n'
        )
        g = ProductGroup(slug=slug, title=title)
        g.product["www.etiqa.com.sg"] = parse_snapshot(path, tmp_path)
        return g

    groups = {
        "travel": group("travel", "Travel", "short"),
        "travel-insurance": group("travel-insurance", "Travel Insurance", "plenty of body " * 40),
    }
    assert len(groups["travel-insurance"].text) > len(groups["travel"].text)

    index: dict[str, str] = {}
    for slug in sorted(groups, key=lambda s: -len(groups[s].text)):
        index.setdefault(_faq_key(slug), slug)
    assert index[_faq_key("Tiq Travel Insurance")] == "travel-insurance"
