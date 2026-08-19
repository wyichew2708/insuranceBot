"""The generator is the part that must not quietly under-produce: a case it
never generates is a question nobody ever asks the bot."""

import datetime as dt
from pathlib import Path

import pytest
from evalgen.generator import TransclusionIndex, benefit_label, generate
from evalgen.schema import Category

from okf import Bundle, PageType

BUNDLE_ROOT = Path(__file__).resolve().parents[3] / "okf"
TODAY = dt.date(2026, 8, 19)


@pytest.fixture(scope="module")
def suite(bundle: Bundle):  # type: ignore[no-untyped-def]
    return generate(bundle, BUNDLE_ROOT, TODAY)


def test_every_current_version_table_row_gets_a_question(bundle: Bundle, suite) -> None:  # type: ignore[no-untyped-def]
    current_rows = set()
    for row in bundle.tables.rows:
        product = next(
            (
                p
                for p in bundle.pages.values()
                if bundle.product_key(p) == row.product and p.frontmatter.version_in_force
            ),
            None,
        )
        if product and product.frontmatter.version_in_force == row.version:
            current_rows.add(row.row_id)
    asked = {c.generated_from for c in suite.cases}
    missing = current_rows - asked
    assert not missing, f"table rows with no generated question: {sorted(missing)}"


def test_every_authored_alias_is_exercised(bundle: Bundle, suite) -> None:  # type: ignore[no-untyped-def]
    questions = " ".join(c.question.lower() for c in suite.cases)
    for page in bundle.pages.values():
        if page.frontmatter.type not in {PageType.product, PageType.concept}:
            continue
        for alias in page.frontmatter.aliases:
            assert alias.lower() in questions, f"alias {alias!r} on {page.id} is never asked about"


def test_case_ids_are_unique(suite) -> None:  # type: ignore[no-untyped-def]
    ids = [c.id for c in suite.cases] + [c.id for c in suite.merge_cases]
    assert len(ids) == len(set(ids))


def test_superseded_versions_expect_a_block_not_an_answer(suite) -> None:  # type: ignore[no-untyped-def]
    historic = [c for c in suite.cases if c.category is Category.historic]
    assert historic, "the bundle has a superseded version; it must be tested"
    for case in historic:
        assert case.expect.expect_delivered is False
        assert "version-coherence" in case.expect.expect_gate_fail


def test_figure_cases_expect_a_bound_row(suite) -> None:  # type: ignore[no-untyped-def]
    figures = [c for c in suite.cases if c.category is Category.figure]
    assert figures
    for case in figures:
        assert case.expect.expect_row_ids, f"{case.id} does not pin a table row"
        assert case.expect.must_contain, f"{case.id} does not pin a value"


def test_conflicts_become_hallucination_bait(suite) -> None:  # type: ignore[no-untyped-def]
    conflicts = [c for c in suite.cases if c.category is Category.conflict]
    assert conflicts, "the seed bundle has a planted site/table disagreement"
    for case in conflicts:
        assert case.expect.must_not_contain


def test_expired_promotion_becomes_a_staleness_case(suite) -> None:  # type: ignore[no-untyped-def]
    stale = [c for c in suite.cases if c.category is Category.staleness]
    assert stale
    assert all(c.expect.must_not_contain for c in stale)


def test_merge_cases_only_for_multi_channel_products(bundle: Bundle, suite) -> None:  # type: ignore[no-untyped-def]
    for case in suite.merge_cases:
        assert len(case.channels) >= 2
    # Private car has a single channel binding, so it cannot have a merge pair.
    assert not [c for c in suite.merge_cases if "private-car" in c.id]


def test_entitlement_cases_run_unauthenticated(suite) -> None:  # type: ignore[no-untyped-def]
    cases = [c for c in suite.cases if c.category is Category.entitlement]
    assert cases
    for case in cases:
        assert case.session.auth_level == "L0"
        assert case.session.policy_id is None
        # "ALL" is a table sentinel, not a leakable tier.
        assert "ALL" not in case.expect.must_not_contain


def test_generation_is_deterministic(bundle: Bundle) -> None:
    first = generate(bundle, BUNDLE_ROOT, TODAY)
    second = generate(bundle, BUNDLE_ROOT, TODAY)
    assert [c.model_dump() for c in first.cases] == [c.model_dump() for c in second.cases]


def test_transclusion_index_maps_tokens_to_pages(bundle: Bundle) -> None:
    index = TransclusionIndex.build(bundle)
    pages = index.pages_for("travel", "medical_expenses", "limit")
    assert "product/general/travel/benefits" in pages


def test_benefit_labels_are_customer_language() -> None:
    assert benefit_label("ncd") == "no-claim discount"
    assert benefit_label("some_new_benefit") == "some new benefit"


def test_suite_grows_with_the_corpus(bundle: Bundle, suite) -> None:  # type: ignore[no-untyped-def]
    # A hand-written suite would not; this is the point of generating.
    assert suite.total > len(bundle.pages)
