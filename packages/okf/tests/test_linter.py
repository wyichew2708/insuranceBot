"""The linter is what stops the wiki rotting into confidently-wrong prose."""

from okf.linter import lint_bundle, lint_page

from okf import Bundle, parse_page

BASE = """---
okf_version: "0.1"
id: product/general/demo
title: Demo
type: product
status: approved
jurisdiction: SG
authority: [raw/wordings/travel-2026.1.md]
effective_from: 2026-01-01
reviewed_by: ["compliance:fixture"]
review_due: 2026-11-18
---

{body}
"""


def rules(body: str, bundle: Bundle) -> set[str]:
    page = parse_page(BASE.format(body=body))
    return {v.rule for v in lint_page(page, bundle)}


def test_seed_bundle_is_clean(bundle: Bundle) -> None:
    report = lint_bundle(bundle)
    assert report.ok, [f"{v.page_id}: {v.message}" for v in report.errors]


def test_unreferenced_claim_is_blocked(bundle: Bundle) -> None:
    body = "## X\n\nTravel insurance covers medical expenses while you are overseas."
    assert "source-ref" in rules(body, bundle)


def test_referenced_claim_passes(bundle: Bundle) -> None:
    body = (
        "## X\n\nTravel insurance covers medical expenses overseas [src:raw/wordings/travel-2026.1.md#s4.2]."
    )
    assert "source-ref" not in rules(body, bundle)


def test_number_in_prose_is_blocked(bundle: Bundle) -> None:
    body = "## X\n\nThe limit is S$500,000 per trip [src:raw/wordings/travel-2026.1.md#s4.2]."
    assert "number-in-prose" in rules(body, bundle)


def test_claim_and_reference_may_wrap_across_lines(bundle: Bundle) -> None:
    body = (
        "## X\n\nThe benefit begins once departure is delayed beyond the stated\n"
        "threshold stated in the schedule\n[src:raw/wordings/travel-2026.1.md#s4.2]."
    )
    assert "source-ref" not in rules(body, bundle)


def test_bare_route_on_a_product_page_is_blocked(bundle: Bundle) -> None:
    # Merge over-flattening guard (§I): a product is one product on every
    # route, so purchase routes live only in channel-variant blocks.
    body = (
        "## X\n\nBuy this at https://www.tiq.com.sg/product/travel-insurance/ "
        "[src:raw/wordings/travel-2026.1.md#s4.2]."
    )
    assert "bare-route" in rules(body, bundle)


def test_naming_the_brand_in_product_prose_is_fine(bundle: Bundle) -> None:
    """There is one brand, so naming it cannot imply a second product — this
    used to be an error and deliberately is not any more."""
    body = "## X\n\nEtiqa covers this loss [src:raw/wordings/travel-2026.1.md#s4.2]."
    assert "bare-route" not in rules(body, bundle)


def test_route_inside_a_channel_variant_block_is_allowed(bundle: Bundle) -> None:
    body = (
        "## How to buy\n\n<!-- okf:channel-variant -->\n"
        "| Channel | Route |\n|---|---|\n"
        "| Direct | https://www.tiq.com.sg/product/travel-insurance/ |\n"
        "<!-- /okf:channel-variant -->"
    )
    assert "bare-route" not in rules(body, bundle)


def test_legal_underwriter_name_is_allowed(bundle: Bundle) -> None:
    body = "## X\n\nThis is one Etiqa Insurance Pte. Ltd. product [src:raw/wordings/travel-2026.1.md#s4.2]."
    assert "bare-brand" not in rules(body, bundle)


def test_pointer_paragraph_needs_no_reference(bundle: Bundle) -> None:
    body = "## X\n\nFull detail is on the [benefits page](./travel/benefits.md)."
    assert "source-ref" not in rules(body, bundle)


def test_broken_link_is_blocked(bundle: Bundle) -> None:
    page = parse_page(
        BASE.format(body="## X\n\nSee it [src:raw/x.md#y].").replace(
            "review_due: 2026-11-18", "review_due: 2026-11-18\nlinks:\n  benefits: product/nope"
        )
    )
    assert "broken-link" in {v.rule for v in lint_page(page, bundle)}


def test_approved_page_needs_signoff(bundle: Bundle) -> None:
    page = parse_page(
        BASE.format(body="## X\n\nSee it [src:raw/x.md#y].")
        .replace('  reviewed_by: ["compliance:fixture"]\n', "")
        .replace('reviewed_by: ["compliance:fixture"]\n', "")
    )
    assert "approval" in {v.rule for v in lint_page(page, bundle)}
