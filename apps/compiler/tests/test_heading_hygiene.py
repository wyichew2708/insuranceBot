"""A heading is a label. It carries no source marker and no HTML entity."""

from __future__ import annotations

from compiler.wiki import CompileReport, _heading, _verbatim


def test_a_source_marker_never_survives_into_a_heading() -> None:
    got = _heading("Emergency medical evacuation [src:raw/wordings/x.md#p3]")
    assert "[src:" not in got
    assert got == "Emergency medical evacuation"


def test_html_entities_are_decoded() -> None:
    assert _heading("Death, Total &amp; Permanent Disability") == "Death, Total & Permanent Disability"


def test_a_quoted_paragraph_does_not_keep_a_heading_marker() -> None:
    # `> ## Exclusions applicable to Section 10` inside a paragraph put a
    # heading in the middle of a claim, and the entailment judge called the
    # result a contradiction — correctly.
    out = _verbatim(
        "## Exclusions applicable to Section 10 and Section 11 of this policy", "raw/x.md", CompileReport()
    )
    assert out is not None
    assert not out.lstrip("> ").startswith("#")
