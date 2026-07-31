from contracts.okf import parse_okf_markdown
from ingestion.validator import lint_bundle

TEMPLATE = """---
okf: '0.2'
id: {id}
type: faq
title: T
product_code: ALL
line: common
audience: {audience}
brand: [tiq]
language: en
jurisdiction: SG
version: 1
status: published
effective_from: {effective_from}
{extra}
---

Body.
"""


def block(id: str, audience: str = "public", effective_from: str = "2026-01-01", extra: str = ""):  # type: ignore[no-untyped-def]
    return parse_okf_markdown(
        TEMPLATE.format(id=id, audience=audience, effective_from=effective_from, extra=extra)
    )


def test_clean_bundle_passes() -> None:
    report = lint_bundle([block("a"), block("b", extra="related: [a]")])
    assert report.ok, report.violations


def test_unresolved_related_link_fails() -> None:
    report = lint_bundle([block("a", extra="related: [missing-block]")])
    assert any("does not resolve" in v for v in report.violations)


def test_overlapping_effective_windows_fail() -> None:
    report = lint_bundle([block("a", effective_from="2026-01-01"), block("a", effective_from="2026-06-01")])
    assert any("overlapping effective windows" in v for v in report.violations)


def test_language_variants_share_id_without_violation() -> None:
    ms = parse_okf_markdown(
        TEMPLATE.format(id="a", audience="public", effective_from="2026-01-01", extra="").replace(
            "language: en", "language: ms"
        )
    )
    report = lint_bundle([block("a"), ms])
    assert report.ok, report.violations


def test_internal_block_linked_from_public_index_fails() -> None:
    report = lint_bundle([block("secret", audience="internal")], public_index_links={"secret"})
    assert any("public index.md links internal block" in v for v in report.violations)


def test_disappearing_id_fails_immutability() -> None:
    report = lint_bundle([block("a")], previous_ids={"a", "gone"})
    assert any("gone" in v and "immutable" in v for v in report.violations)
