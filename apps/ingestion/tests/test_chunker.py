"""Chunker boundary tests (§11)."""

from contracts.okf import parse_okf_markdown
from ingestion.chunker import MAX_BLOCK_TOKENS, chunk_block, slugify, split_sections

FM = """---
okf: '0.2'
id: tiq-trv/faq/sample
type: faq
title: Sample FAQ
product_code: TIQ-TRV
line: personal/travel
audience: public
brand: [tiq]
language: en
jurisdiction: SG
version: 1
status: published
effective_from: 2026-01-01
---

"""


def block_with_body(body: str):  # type: ignore[no-untyped-def]
    return parse_okf_markdown(FM + body)


def test_small_block_is_one_chunk_with_block_id() -> None:
    block = block_with_body("## Overview\n\nShort answer.")
    chunks = chunk_block(block)
    assert len(chunks) == 1
    assert chunks[0].chunk_id == "tiq-trv/faq/sample"
    assert chunks[0].embed_text.startswith("Sample FAQ")


def test_large_block_splits_per_section_never_mid_section() -> None:
    section = "## Part {n}\n\n" + ("word " * (MAX_BLOCK_TOKENS // 2)) + "\n\n"
    body = "".join(section.replace("{n}", str(n)) for n in range(1, 4))
    chunks = chunk_block(block_with_body(body))
    assert [c.chunk_id for c in chunks] == [
        "tiq-trv/faq/sample#part-1",
        "tiq-trv/faq/sample#part-2",
        "tiq-trv/faq/sample#part-3",
    ]
    for c in chunks:
        assert c.text.startswith("## Part")
        assert c.block_id == "tiq-trv/faq/sample"


def test_duplicate_headings_get_unique_chunk_ids() -> None:
    body = ("## Steps\n\n" + "word " * 400 + "\n\n") * 2
    chunks = chunk_block(block_with_body(body))
    assert [c.chunk_id for c in chunks] == [
        "tiq-trv/faq/sample#steps",
        "tiq-trv/faq/sample#steps-2",
    ]


def test_leading_unheaded_text_becomes_intro_chunk() -> None:
    body = "Lead-in paragraph. " * 200 + "\n\n## Details\n\n" + "word " * 600
    chunks = chunk_block(block_with_body(body))
    assert chunks[0].chunk_id.endswith("#intro")
    assert chunks[1].chunk_id.endswith("#details")


def test_split_sections_and_slugify() -> None:
    assert split_sections("") == []
    assert slugify("Pre-Existing Conditions?") == "pre-existing-conditions"
    assert slugify("!!!") == "section"
