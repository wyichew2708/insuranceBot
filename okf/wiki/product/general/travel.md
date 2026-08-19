---
okf_version: "0.1"
id: product/general/travel
title: Travel Insurance
type: product
status: approved
lifecycle: on_sale
underwriter: Etiqa Insurance Pte. Ltd.
uen: "201331905K"
jurisdiction: SG
line_of_business: general
regulated_advice: false
aliases: ["Tiq Travel", "Etiqa Travel Insurance", "travel plan", "travel cover", "trip insurance"]
channels:
  - ref: channel/tiq-sg
    brand: Tiq
    purchase: direct_online
    landing: https://www.tiq.com.sg/product/travel-insurance/
    hotline: "+65 6887 8777"
  - ref: channel/etiqa-sg
    brand: Etiqa
    purchase: online_or_adviser
    landing: https://www.etiqa.com.sg/personal/travel-insurance/
    hotline: "+65 6336 0477"
plan_tiers: ["tier-1", "tier-2", "tier-3"]
authority:
  - raw/wordings/travel-2026.1.md
  - raw/product-summaries/travel-2026.1.md
  - raw/web/etiqa-sg/2026-08-18-travel.md
  - raw/web/tiq-sg/2026-08-18-travel.md
version_in_force: "2026.1"
effective_from: 2026-01-01
effective_to: null
links:
  benefits: product/general/travel/benefits
  exclusions: product/general/travel/exclusions
  claims: journey/claim/travel
  concepts: [concept/pre-existing-condition, concept/travel-delay, concept/excess]
compiled_from_commit: seed
compiled_at: 2026-08-18T02:14:00+08:00
reviewed_by: ["product-owner:fixture", "compliance:fixture"]
review_due: 2026-11-18
confidence: high
---

## What this plan is

Travel Insurance is a single-trip or annual policy covering overseas medical
expenses, trip cancellation, travel delay and baggage [src:raw/product-summaries/travel-2026.1.md#medical].

Coverage, limits and exclusions are identical across channels — this is one
Etiqa Insurance Pte. Ltd. product [src:raw/wordings/travel-2026.1.md#s4.2].

## Headline benefits

The overseas medical expenses limit for the plan tier held is
{{table:medical_expenses.limit}} [src:raw/product-summaries/travel-2026.1.md#medical].

Travel delay pays once departure is delayed beyond
{{table:travel_delay.threshold_hours}}, then per completed block thereafter
[src:raw/wordings/travel-2026.1.md#s4.2].

Full benefit detail is on the [benefits page](./travel/benefits.md).

## What is not covered

Exclusions are listed in full on the [exclusions page](./travel/exclusions.md),
and pre-existing conditions are the most commonly missed one [src:raw/wordings/travel-2026.1.md#s6.1].

## How to buy

<!-- okf:channel-variant -->
| Channel | Route | Contact |
|---|---|---|
| Tiq (direct online) | {{channel.tiq-sg.landing}} | {{channel.tiq-sg.hotline}} |
| Etiqa (online or adviser) | {{channel.etiqa-sg.landing}} | {{channel.etiqa-sg.hotline}} |
<!-- /okf:channel-variant -->

Current promotions are not listed here; see [promotions](../../promotion/index.md),
which are effective-dated and may be channel-specific
[src:raw/web/tiq-sg/2026-08-18-travel.md#title].
