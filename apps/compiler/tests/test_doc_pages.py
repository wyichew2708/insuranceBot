"""The document tiers reaching the wiki.

The website says what a product is for; the wording says what it covers. Until
the compiler read the wordings, every exclusions page in the real bundle said
the exclusions could not be extracted while the contract sat unread on disk.
These tests pin the two halves of the fix: the contract's own text reaches a
page, and the contract's own figures survive the numeric gate by being quoted
rather than retyped.
"""

from __future__ import annotations

import asyncio
import datetime as dt
from pathlib import Path

import httpx
import pytest
from compiler.wiki import CompileConfig, _verbatim, compile_bundle
from crawler.crawl import USER_AGENT, CrawlConfig, crawl

from fixtures.synthetic_site import ETIQA, TIQ, transport
from okf import Bundle, lint_bundle

TODAY = dt.date(2026, 8, 19)

TRAVEL_WORDING = """---
source_url: "https://www.etiqa.com.sg/wp-content/uploads/2026/01/Travel.pdf"
tier: "wordings"
pages: 2
---
Travel Insurance

General Exclusions

We will not pay for any claim arising from a pre-existing medical condition.

We will not pay for loss caused by war, invasion or civil commotion.

Page 1 of 2

How To Make A Claim

You must notify Us within thirty (30) days of the event, and the excess of
S$250 applies to each claim.
"""

ORPHAN_WORDING = """---
source_url: "https://www.etiqa.com.sg/wp-content/uploads/2023/02/Fidelity.pdf"
tier: "wordings"
pages: 1
---
Fidelity Guarantee

What Is Covered

This policy indemnifies the Employer against direct pecuniary loss caused by
an act of fraud or dishonesty committed by an Employee.

General Exclusions

We do not cover loss discovered more than twelve months after the Employee
left Your service.
"""


@pytest.fixture(scope="module")
def compiled(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("bundle")

    async def run() -> None:
        config = CrawlConfig(allowlist=[ETIQA, TIQ], out_dir=root / "raw", requests_per_second=0, today=TODAY)
        async with httpx.AsyncClient(
            transport=transport(), headers={"User-Agent": USER_AGENT}, follow_redirects=True
        ) as client:
            await crawl(config, client)

    asyncio.run(run())
    wordings = root / "raw" / "wordings"
    wordings.mkdir(parents=True, exist_ok=True)
    (wordings / "travel-policy-wording-v1-26.md").write_text(TRAVEL_WORDING)
    (wordings / "fidelity-guarantee-policy-wording-2023-02.md").write_text(ORPHAN_WORDING)
    compile_bundle(CompileConfig(source_root=root, dest_root=root, today=TODAY, sign_off=["compliance:test"]))
    return root


@pytest.fixture(scope="module")
def bundle(compiled: Path) -> Bundle:
    return Bundle.load(compiled)


def test_the_wording_replaces_the_placeholder_exclusions(bundle: Bundle) -> None:
    page = bundle.get("product/general/travel/exclusions")
    assert page is not None
    assert "could not be extracted" not in page.body
    assert "pre-existing medical condition" in page.body
    assert "raw/wordings/travel-policy-wording-v1-26.md" in page.body


def test_the_contract_outranks_the_website_it_is_merged_with(bundle: Bundle) -> None:
    """The wording does not refute the marketing summary, so both are kept —
    but authority is declared, and page order follows it."""
    page = bundle.get("product/general/travel/exclusions")
    assert page is not None
    assert page.frontmatter.authority[0] == "raw/wordings/travel-policy-wording-v1-26.md"
    assert any(ref.startswith("raw/web/") for ref in page.frontmatter.authority)
    contract = page.body.index("pre-existing medical condition")
    website = page.body.index("raw/web/")
    assert contract < website


def test_a_product_that_exists_only_as_a_pdf_gets_a_page(bundle: Bundle) -> None:
    """A third of this insurer's book has a wording and no crawlable page.
    Before this, asking about any of it retrieved nothing."""
    page = bundle.get("product/general/fidelity-guarantee")
    assert page is not None
    assert page.frontmatter.title == "Fidelity Guarantee Insurance"
    # No crawled page means no marketing claim to cross-check against.
    assert page.frontmatter.channels == []
    assert bundle.get("product/general/fidelity-guarantee/exclusions") is not None


def test_every_document_page_is_reachable_from_the_index(bundle: Bundle) -> None:
    index = bundle.get("index")
    assert index is not None
    assert "product/general/fidelity-guarantee" in index.body
    assert "product/general/travel/claims" in index.body


def test_the_bundle_still_lints(bundle: Bundle) -> None:
    report = lint_bundle(bundle)
    assert report.errors == []


def test_a_clause_with_a_figure_is_quoted_not_retyped() -> None:
    """Rule 2 says numbers never live in prose. A contract's notice period
    cannot become a benefit-table row either, so the third option is to
    reproduce the clause and name the page it came from."""
    from compiler.wiki import CompileReport

    report = CompileReport()
    line = _verbatim("You must notify Us within thirty (30) days.", "raw/wordings/travel.md#p2", report)
    assert line is not None and line.startswith("> ")
    assert "[src:raw/wordings/travel.md#p2]" in line


def test_a_clause_without_a_figure_stays_ordinary_prose() -> None:
    from compiler.wiki import CompileReport

    line = _verbatim("We will not pay for loss caused by war.", "raw/wordings/t.md", CompileReport())
    assert line is not None and not line.startswith(">")
    assert line.endswith("[src:raw/wordings/t.md].")


def test_a_clause_carrying_a_hotline_is_dropped() -> None:
    """Contact details vary by distribution route; the renderer substitutes
    the session's own. Baking one into a product page is the merge
    over-flattening the linter's bare-route rule exists to catch."""
    report = CompileReport = None  # noqa: F841
    from compiler.wiki import CompileReport as Report

    report = Report()
    assert _verbatim("Please call us on +65 6887 8777 to claim.", "raw/wordings/t.md", report) is None
    assert any("channel-varying" in reason for reason in report.skipped)
