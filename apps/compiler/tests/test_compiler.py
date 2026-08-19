"""Loop 2 — fact extraction, conflict detection, impact analysis."""

from pathlib import Path

from compiler.conflicts import DEFAULT_AUTHORITY, authority_rank, detect_conflicts, scan, write_conflicts
from compiler.facts import SourceDoc, extract_facts
from compiler.impact import pages_citing, recompile_queue

from okf import BenefitTables, Bundle

BUNDLE_ROOT = Path(__file__).resolve().parents[3] / "okf"


def test_content_hash_is_stable() -> None:
    a = SourceDoc(path="raw/x.md", text="hello")
    b = SourceDoc(path="raw/x.md", text="hello")
    assert a.content_hash == b.content_hash
    assert a.content_hash != SourceDoc(path="raw/x.md", text="hello!").content_hash


def test_extraction_is_typed_with_source_and_locator() -> None:
    doc = SourceDoc(
        path="raw/web/tiq-sg/x.md",
        text="## delay\nTravel delay benefit starts after 4 hours of delay.\n",
    )
    facts = extract_facts(doc)
    assert facts
    fact = facts[0]
    assert fact.benefit_code == "travel_delay"
    assert fact.value == "4"
    assert fact.locator == "delay"
    assert fact.source_path == "raw/web/tiq-sg/x.md"


def test_authority_order_ranks_wordings_above_marketing() -> None:
    assert authority_rank("raw/wordings/travel.md", DEFAULT_AUTHORITY) < authority_rank(
        "raw/web/tiq-sg/travel.md", DEFAULT_AUTHORITY
    )
    assert authority_rank("raw/unknown/x.md", DEFAULT_AUTHORITY) == len(DEFAULT_AUTHORITY)


def test_website_contradicting_the_benefit_table_is_a_conflict() -> None:
    conflicts = scan(BUNDLE_ROOT)
    delay = [c for c in conflicts if c.benefit_code == "travel_delay"]
    assert delay, "the planted 4-hour website claim must be caught"
    conflict = delay[0]
    assert conflict.winner.source_path == "raw/benefit-tables"
    assert "tiq-sg" in conflict.loser.source_path
    assert conflict.winner.value == "6" and conflict.loser.value == "4"


def test_a_value_present_in_any_tier_is_not_a_conflict() -> None:
    # The site quotes S$1,000,000 "on our top plan", which is the tier-3 row.
    conflicts = scan(BUNDLE_ROOT)
    assert not [c for c in conflicts if c.benefit_code == "medical_expenses"]


def test_conflict_is_written_as_a_website_defect_ticket(tmp_path: Path) -> None:
    conflicts = scan(BUNDLE_ROOT)
    written = write_conflicts(tmp_path, conflicts[:1])
    text = written[0].read_text()
    assert "website defect" in text
    assert "Status:** open" in text


def test_no_conflict_when_sources_agree() -> None:
    docs = [
        SourceDoc(path="raw/wordings/a.md", text="## d\nTravel delay after 6 hours.\n"),
        SourceDoc(path="raw/web/tiq-sg/a.md", text="## d\nTravel delay after 6 hours.\n"),
    ]
    tables = BenefitTables.from_dir(BUNDLE_ROOT / "raw" / "benefit-tables")
    assert detect_conflicts(docs, DEFAULT_AUTHORITY, tables) == []


def test_impact_analysis_finds_pages_citing_a_source() -> None:
    bundle = Bundle.load(BUNDLE_ROOT)
    pages = pages_citing(bundle, "raw/wordings/travel-2026.1.md")
    assert "product/general/travel/exclusions" in pages
    assert "product/motor/private-car" not in pages


def test_recompile_queue_is_the_union_and_deduplicated() -> None:
    bundle = Bundle.load(BUNDLE_ROOT)
    queue = recompile_queue(
        bundle, ["raw/wordings/travel-2026.1.md", "raw/product-summaries/travel-2026.1.md"]
    )
    assert queue == sorted(set(queue))
    assert len(queue) < len(bundle.pages), "impact analysis must not recompile the whole bundle"
