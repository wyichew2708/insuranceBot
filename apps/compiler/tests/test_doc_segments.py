"""Turning an extracted PDF back into structure.

These fixtures are shaped like what the extractors actually emit, because
every rule in `compiler.documents` exists to survive a specific artefact of
that output: hard-wrapped lines, folios printed on every page, revision
stamps that look exactly like headings, and one backend that emits no blank
lines at all.
"""

from __future__ import annotations

from pathlib import Path

from compiler.documents import (
    CAMPAIGN_RE,
    DocSection,
    Document,
    load_documents,
    looks_like_heading,
    match_documents,
    normalise_plan,
    parse_document,
    role_for,
    segment,
)

WORDING = """---
source_url: "https://www.etiqa.com.sg/wp-content/uploads/2025/10/Tiq-Home.pdf"
tier: "wordings"
pages: 3
---
Tiq Home Insurance

General Definitions

Accident refers to an unexpected and unintentional event that is violent,
visible and external.

Excess refers to the first amount of each claim that You must pay.

V9.0 | 20 October 2023

Page 1 of 3

General Exclusions

We will not pay for loss caused by war, invasion or civil commotion.

We will not pay for wear and tear.

Page 2 of 3

How To Make A Claim

You must notify Us within thirty (30) days of the event.
"""


def _doc(text: str = WORDING, tmp: Path | None = None) -> Document:
    assert tmp is not None
    path = tmp / "tiq-home-policy-wording-v9-20-oct-2023.md"
    path.write_text(text)
    return parse_document(path, f"raw/wordings/{path.name}", "wordings")


def test_sections_are_classified_by_role(tmp_path: Path) -> None:
    document = _doc(tmp=tmp_path)
    roles = {s.role: s.heading for s in document.sections}
    assert roles["definitions"] == "General Definitions"
    assert roles["exclusions"] == "General Exclusions"
    assert roles["claims"] == "How To Make A Claim"


def test_a_revision_stamp_does_not_open_a_section(tmp_path: Path) -> None:
    """`V9.0 | 20 October 2023` is short and title-cased, so the heading test
    accepts its shape. Letting it through splits a policy at every page foot:
    on the real corpus 110,000 words filed themselves under "v1.25"."""
    document = _doc(tmp=tmp_path)
    assert not any(h.lower().startswith("v9") for h in (s.heading.lower() for s in document.sections))


def test_a_section_survives_the_page_break_it_was_printed_over(tmp_path: Path) -> None:
    exclusions = next(s for s in document_sections(tmp_path) if s.role == "exclusions")
    assert len(exclusions.paragraphs) == 2
    assert "wear and tear" in exclusions.paragraphs[1]


def document_sections(tmp_path: Path) -> list[DocSection]:
    return _doc(tmp=tmp_path).sections


def test_the_locator_names_the_printed_page(tmp_path: Path) -> None:
    document = _doc(tmp=tmp_path)
    claims = next(s for s in document.sections if s.role == "claims")
    assert document.locator(claims).endswith("#p3")


def test_paragraphs_are_rebuilt_from_wrapped_lines(tmp_path: Path) -> None:
    document = _doc(tmp=tmp_path)
    definitions = next(s for s in document.sections if s.role == "definitions")
    assert "violent, visible and external" in definitions.paragraphs[0]


def test_headings_are_found_without_blank_lines() -> None:
    """One backend emits 2,470 lines with 89 blanks. Blank-line paragraph
    detection alone reads that document as a single 24,000-word section."""
    dense = "\n".join(
        [
            "Table of Benefits",
            "Personal Accident applies to every insured person named in the schedule.",
            "General Exclusions",
            "We do not cover claims arising from a pre-existing condition.",
        ]
    )
    roles = [s.role for s in segment(dense)]
    assert "exclusions" in roles
    assert "benefits" in roles


def test_a_sentence_fragment_is_not_a_heading() -> None:
    assert looks_like_heading("General Exclusions")
    assert looks_like_heading("What do we mean with these words?")
    assert not looks_like_heading("We will not pay for wear and tear.")
    assert not looks_like_heading("Adult aged below 70 years old")
    assert not looks_like_heading("201331905K")


def test_qualified_headings_resolve_to_the_narrower_role() -> None:
    assert role_for("Benefit Exclusions") == "exclusions"
    assert role_for("What do we mean with these words?") == "definitions"
    assert role_for("Section 4 - Baggage Delay") == "benefits"
    assert role_for("Free Look Period") == "conditions"
    assert role_for("Table of Contents") == "contents"


def test_version_and_kind_are_stripped_from_the_file_name() -> None:
    assert normalise_plan("policy-contract-for-early-ci-rider-v1-23") == "early-ci-rider"
    assert normalise_plan("tiq-home-policy-wording-v9-20-oct-2023-final") == "tiq-home"
    assert normalise_plan("etiqa-fire-policy-wording") == "etiqa-fire"


def test_campaign_paperwork_is_not_a_product() -> None:
    """The ingest tiers by file name, and an insurer calls both its contracts
    and its lucky draws "terms and conditions"."""
    assert CAMPAIGN_RE.search("sg-pet-festival-2025-spin-and-win-tnc")
    assert CAMPAIGN_RE.search("cny-2024-campaign-terms-and-conditions")
    assert not CAMPAIGN_RE.search("private-car-insurance-policy-wording-v19")


def test_the_shopfront_prefix_does_not_prevent_a_match(tmp_path: Path) -> None:
    document = _doc(tmp=tmp_path)
    matched, unmatched = match_documents([document], ["home", "travel"])
    assert "home" in matched
    assert not unmatched


def test_one_contract_governs_every_front_door(tmp_path: Path) -> None:
    """The crawl yields a product page per front door — `home-insurance` from
    one host, `tiq-home-insurance` from the other — and they are the same
    policy. Attaching the wording to only one leaves the other answering from
    marketing copy."""
    document = _doc(tmp=tmp_path)
    matched, unmatched = match_documents([document], ["home-insurance", "tiq-home-insurance", "travel"])
    assert sorted(matched) == ["home-insurance", "tiq-home-insurance"]
    assert not unmatched


def test_a_near_name_does_not_claim_a_different_product(tmp_path: Path) -> None:
    """Without the overlap threshold `home` takes `home-renewal-protection-
    bundle` as readily as `tiq-home` does."""
    document = _doc(tmp=tmp_path)
    matched, unmatched = match_documents([document], ["home-renewal-protection-bundle"])
    assert not matched
    assert len(unmatched) == 1


def test_an_unknown_plan_is_reported_rather_than_forced(tmp_path: Path) -> None:
    document = _doc(tmp=tmp_path)
    matched, unmatched = match_documents([document], ["private-car"])
    assert not matched
    assert [d.plan for d in unmatched] == ["tiq-home"]


def test_load_skips_campaigns_and_reads_both_tiers(tmp_path: Path) -> None:
    for tier, name in (
        ("wordings", "tiq-home-policy-wording.md"),
        ("wordings", "cny-2024-campaign-terms-and-conditions.md"),
        ("product-summaries", "tiq-home-product-summary.md"),
        ("brochures", "tiq-home-brochure.md"),
    ):
        directory = tmp_path / "raw" / tier
        directory.mkdir(parents=True, exist_ok=True)
        (directory / name).write_text(WORDING)
    loaded = load_documents(tmp_path)
    assert sorted(d.tier for d in loaded) == ["product-summaries", "wordings"]


def test_wordings_outrank_product_summaries(tmp_path: Path) -> None:
    for tier in ("wordings", "product-summaries"):
        directory = tmp_path / "raw" / tier
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "tiq-home-policy-wording.md").write_text(WORDING)
    matched, _ = match_documents(load_documents(tmp_path), ["home"])
    assert [d.tier for d in matched["home"]] == ["wordings", "product-summaries"]
