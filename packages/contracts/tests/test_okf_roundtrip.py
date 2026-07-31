"""Round-trip: yaml frontmatter -> model -> yaml (§4 DoD)."""

import pytest
import yaml
from contracts.okf import OkfBlock, parse_okf_markdown, render_okf_markdown

DOC = """---
okf: '0.2'
id: tiq-trv/exclusions/pre-existing-conditions
type: exclusion
title: Pre-existing conditions
product_code: TIQ-TRV
line: personal/travel
audience: public
brand: [tiq]
language: en
jurisdiction: SG
version: 3
status: published
effective_from: 2026-01-01
related: [tiq-trv/faq/what-is-covered]
tags: [travel, exclusion]
---

## Overview

Pre-existing medical conditions are not covered unless the add-on is purchased.
"""


def test_roundtrip_preserves_semantics() -> None:
    block = parse_okf_markdown(DOC)
    rendered = render_okf_markdown(block)
    reparsed = parse_okf_markdown(rendered)
    assert reparsed.frontmatter == block.frontmatter
    assert reparsed.body.strip() == block.body.strip()

    original_fm = yaml.safe_load(DOC.split("---")[1])
    roundtrip_fm = yaml.safe_load(rendered.split("---")[1])
    for key, value in original_fm.items():
        assert str(roundtrip_fm[key]) == str(value) or roundtrip_fm[key] == value


def test_unknown_keys_preserved_and_reported() -> None:
    doc = DOC.replace("tags: [travel, exclusion]", "tags: [travel]\nfuture_key: hello")
    block = parse_okf_markdown(doc)
    assert block.frontmatter.unknown_keys() == ["future_key"]
    assert "future_key" in render_okf_markdown(block)


def test_missing_required_field_fails() -> None:
    doc = DOC.replace("title: Pre-existing conditions\n", "")
    with pytest.raises(ValueError):
        parse_okf_markdown(doc)


def test_unsupported_okf_version_fails() -> None:
    doc = DOC.replace("okf: '0.2'", "okf: '9.9'")
    with pytest.raises(ValueError):
        parse_okf_markdown(doc)


def test_block_model_roundtrips_via_json() -> None:
    block = parse_okf_markdown(DOC)
    assert OkfBlock.model_validate_json(block.model_dump_json()) == block
