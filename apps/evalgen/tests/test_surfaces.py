"""The phrasing layer: one fact, many questions, one expectation."""

import datetime as dt
from pathlib import Path

from evalgen.generator import (
    _brands,
    _excluded_subject,
    _product_pages,
    generate,
    per_product_counts,
    per_product_facts,
)
from evalgen.schema import Category
from evalgen.surfaces import BRAND_CONFUSION, FigureFact, figure_surfaces, short

from okf import Bundle

BUNDLE_ROOT = Path(__file__).resolve().parents[3] / "okf"
TODAY = dt.date(2026, 8, 19)


def _fact(**over: object) -> FigureFact:
    base: dict[str, object] = dict(
        title="Travel Insurance",
        benefit_code="baggage_loss",
        benefit="baggage",
        attribute="limit",
        attribute_text="limit",
        value="S$3,000",
        tier="tier-1",
        canonical="What is the baggage limit on Travel Insurance?",
        aliases=("Tiq Travel",),
        brands=("Etiqa", "Tiq"),
        seed=0,
    )
    base.update(over)
    return FigureFact(**base)  # type: ignore[arg-type]


def test_short_drops_the_suffix_customers_omit() -> None:
    assert short("Travel Insurance") == "travel"
    assert short("Private Car Insurance") == "private car"
    # Nothing to strip is not an error, just a shorter name.
    assert short("Home") == "home"


def test_a_family_asks_one_fact_many_ways_without_repeating_itself() -> None:
    surfaces = figure_surfaces(_fact())
    questions = [s.question for s in surfaces]
    assert len(questions) == len(set(questions))
    assert len(surfaces) >= 8
    assert surfaces[0].kind == "canonical"


def test_surfaces_that_withhold_vocabulary_are_marked_loose() -> None:
    by_kind = {s.kind: s for s in figure_surfaces(_fact())}
    # These name the product and the benefit, so an exact citation is fair.
    assert by_kind["canonical"].strict and by_kind["plain"].strict
    # These deliberately do not.
    assert not by_kind["elliptical"].strict
    assert not by_kind["scenario"].strict


def test_the_scenario_surface_never_names_the_benefit() -> None:
    """Its whole purpose is to make the retriever resolve intent rather than
    match a word, so leaking the benefit code back into it would void it."""
    scenario = next(s for s in figure_surfaces(_fact()) if s.kind == "scenario")
    assert "baggage" not in scenario.question.lower()


def test_ceiling_only_attributes_get_the_superlative_form() -> None:
    kinds = {s.kind for s in figure_surfaces(_fact(attribute="limit"))}
    assert "superlative" in kinds
    # An excess is a floor: "the most it will pay" would be a different fact.
    kinds = {s.kind for s in figure_surfaces(_fact(attribute="excess", benefit_code="own_damage"))}
    assert "superlative" not in kinds


def test_untiered_rows_get_no_tier_question() -> None:
    assert "tiered" not in {s.kind for s in figure_surfaces(_fact(tier="ALL"))}
    assert "tiered" in {s.kind for s in figure_surfaces(_fact(tier="tier-2"))}


def test_brand_surfaces_name_one_front_door() -> None:
    brand = next(s for s in figure_surfaces(_fact()) if s.kind == "brand")
    assert brand.category is Category.brand
    assert "Etiqa" in brand.question or "Tiq" in brand.question


def test_excluded_subject_reads_the_corpus_sentence() -> None:
    assert _excluded_subject("Wear and tear, gradual deterioration and pest damage are excluded") == (
        "wear and tear, gradual deterioration and pest damage"
    )
    assert _excluded_subject("Contents are covered up to the limit") is None
    # A whole paragraph is not a noun phrase a question can be built on.
    assert _excluded_subject(" ".join(["word"] * 30) + " are excluded") is None


def test_a_product_is_only_asked_about_under_names_it_publishes(bundle: Bundle) -> None:
    pages = {p.id: p for p in _product_pages(bundle)}
    assert set(_brands(pages["product/general/travel"])) == {"Etiqa", "Tiq"}
    # Private car publishes one front door, so "Tiq private car" is not a
    # question anyone should be asked to answer.
    assert set(_brands(pages["product/motor/private-car"])) == {"Etiqa"}


def test_every_paraphrase_carries_its_canonical_expectation(bundle: Bundle) -> None:
    suite = generate(bundle, BUNDLE_ROOT, TODAY)
    by_id = {c.id: c for c in suite.cases}
    checked = 0
    for case in suite.cases:
        if not case.paraphrase_of or case.category is Category.brand:
            continue
        parent = by_id.get(case.paraphrase_of)
        if parent is None or not parent.expect.expect_row_ids:
            continue
        # The figure assertions are the controlled variable; only must_cite is
        # allowed to relax when the phrasing withholds the vocabulary for it.
        assert case.expect.expect_row_ids == parent.expect.expect_row_ids
        assert case.expect.must_contain == parent.expect.must_contain
        checked += 1
    assert checked > 50


def test_brand_cases_assert_no_disambiguation_prompt(bundle: Bundle) -> None:
    """The merge in one assertion: naming a front door must not come back as a
    question about which front door."""
    suite = generate(bundle, BUNDLE_ROOT, TODAY)
    brand_cases = [c for c in suite.cases if c.category is Category.brand]
    assert brand_cases
    for case in brand_cases:
        assert set(BRAND_CONFUSION) <= set(case.expect.must_not_contain)


def test_case_ids_are_unique(bundle: Bundle) -> None:
    """The runner keys results by id, so a collision drops a case from the
    report without anything reporting that it did."""
    suite = generate(bundle, BUNDLE_ROOT, TODAY)
    ids = [c.id for c in suite.cases]
    assert len(ids) == len(set(ids))


def test_cross_product_cases_are_not_counted_toward_any_product(bundle: Bundle) -> None:
    suite = generate(bundle, BUNDLE_ROOT, TODAY)
    counts = per_product_counts(suite)
    unattributed = [c for c in suite.cases if not c.product]
    assert unattributed, "concept and channel cases belong to no single product"
    assert sum(counts.values()) + len(unattributed) >= len(suite.cases)


def test_the_suite_reports_facts_alongside_cases(bundle: Bundle) -> None:
    """A hundred questions off four facts is paraphrase depth, not coverage,
    and the stats have to make that visible."""
    suite = generate(bundle, BUNDLE_ROOT, TODAY)
    facts = per_product_facts(suite)
    counts = per_product_counts(suite)
    assert set(facts) == set(counts)
    for product, n in counts.items():
        assert 0 < facts[product] < n


def test_gap_probes_expect_a_handoff_only_where_the_corpus_is_silent(bundle: Bundle) -> None:
    suite = generate(bundle, BUNDLE_ROOT, TODAY)
    probes = {c.id: c for c in suite.cases if c.id.startswith("gap-")}
    # Travel publishes a claims journey, so its claim probe must be answered.
    claim = probes["gap-product-general-travel-claim"]
    assert claim.expect.expect_handoff is None
    assert "journey/claim/travel" in claim.expect.relevant_pages
    # No product in this corpus carries a premium, so every premium probe must
    # hand off rather than improvise a price.
    for case_id, case in probes.items():
        if "-premium" in case_id:
            assert case.expect.expect_handoff is True
