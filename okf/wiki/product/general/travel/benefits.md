---
okf_version: "0.1"
id: product/general/travel/benefits
title: Travel Insurance — Benefits
type: product
status: approved
lifecycle: on_sale
underwriter: Etiqa Insurance Pte. Ltd.
jurisdiction: SG
line_of_business: general
aliases: ["travel benefits", "travel limits", "what does travel cover"]
plan_tiers: ["tier-1", "tier-2", "tier-3"]
authority:
  - raw/wordings/travel-2026.1.md
  - raw/product-summaries/travel-2026.1.md
version_in_force: "2026.1"
effective_from: 2026-01-01
links:
  exclusions: product/general/travel/exclusions
  concepts: [concept/travel-delay]
compiled_from_commit: seed
reviewed_by: ["product-owner:fixture", "compliance:fixture"]
review_due: 2026-11-18
confidence: high
---

## Overseas medical expenses

Reimbursed up to {{table:medical_expenses.limit}} for the plan tier held, and
emergency dental treatment following an accident is included [src:raw/product-summaries/travel-2026.1.md#medical].

## Travel delay

The benefit begins once departure of the scheduled conveyance is delayed beyond
{{table:travel_delay.threshold_hours}} [src:raw/wordings/travel-2026.1.md#s4.2].

Thereafter it pays {{table:travel_delay.payout_per_block}} per completed block,
up to a benefit cap of {{table:travel_delay.cap}} [src:raw/product-summaries/travel-2026.1.md#delay].

## Baggage

Accidental loss of or damage to baggage is covered to
{{table:baggage_loss.limit}}, with a per-item sub-limit of
{{table:baggage_loss.per_item_limit}} [src:raw/product-summaries/travel-2026.1.md#baggage].

A police or carrier report must be obtained promptly after discovering the loss
[src:raw/wordings/travel-2026.1.md#s4.7].

## Trip cancellation

Cancellation for a covered reason is reimbursed up to
{{table:trip_cancellation.limit}} [src:raw/product-summaries/travel-2026.1.md#cancellation].
