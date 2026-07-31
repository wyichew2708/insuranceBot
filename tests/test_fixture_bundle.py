"""Phase 1 DoD slice: the fixture bundle loads, lints clean, and chunks."""

import json
from pathlib import Path

from ingestion.chunker import chunk_block
from ingestion.loader import load_bundle
from ingestion.validator import lint_bundle

BUNDLE = Path(__file__).parent.parent / "evals" / "fixture-bundle"


def test_fixture_bundle_loads_and_lints() -> None:
    blocks = load_bundle(BUNDLE)
    assert len(blocks) >= 10
    report = lint_bundle(blocks)
    assert report.ok, report.violations


def test_fixture_bundle_chunks_without_split() -> None:
    blocks = load_bundle(BUNDLE)
    for block in blocks:
        chunks = chunk_block(block)
        assert chunks, block.frontmatter.id
        # all fixture blocks are < 700 tokens: exactly one chunk, id == block id
        assert chunks[0].chunk_id == block.frontmatter.id


def test_fixture_contains_required_scenarios() -> None:
    blocks = load_bundle(BUNDLE)
    by_id = {b.frontmatter.id: b for b in blocks}
    assert "common/escalation/overseas-emergency" in by_id
    assert "common/payments/premium-bank-transfer" in by_id
    internal = [b for b in blocks if b.frontmatter.audience.value == "internal"]
    assert internal, "fixture must contain an internal block for leak tests"


def test_catalogue_block_refs_resolve() -> None:
    blocks = load_bundle(BUNDLE)
    ids = {b.frontmatter.id for b in blocks}
    products = json.loads((BUNDLE / "catalogue" / "products.json").read_text())
    for product in products:
        for ref in product["block_refs"]:
            assert ref in ids, ref
        for benefit in product["benefits"].values():
            assert benefit["block_ref"] in ids


def test_actions_registry_shape() -> None:
    actions = json.loads((BUNDLE / "actions.json").read_text())
    assert {a["action_id"] for a in actions} >= {
        "emergency-services-hotline",
        "customer-hotline",
        "customer-portal",
        "get-advice",
    }
    for action in actions:
        assert action["kind"] in {"link", "phone", "email"}
        assert action["brand"] in {"tiq", "etiqa"}


def test_emergency_hotline_verbatim_consistency() -> None:
    """The escalation block's hotline digits must exactly match the actions
    registry (hard product rule 2 at data level)."""
    blocks = load_bundle(BUNDLE)
    escalation = next(b for b in blocks if b.frontmatter.id == "common/escalation/overseas-emergency")
    actions = json.loads((BUNDLE / "actions.json").read_text())
    hotline = next(a for a in actions if a["action_id"] == "emergency-services-hotline")
    assert hotline["value"] in escalation.body
