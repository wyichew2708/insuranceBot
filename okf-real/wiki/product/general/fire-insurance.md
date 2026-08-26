---
okf_version: '0.1'
id: product/general/fire-insurance
title: HDB Fire Insurance
type: product
status: approved
lifecycle: on_sale
underwriter: Etiqa Insurance Pte. Ltd.
uen: 201331905K
jurisdiction: SG
line_of_business: general
regulated_advice: false
aliases:
- hdb fire
channels:
- ref: channel/direct
  name: Direct
  purchase: direct_online
  landing: https://www.etiqa.com.sg/personal/fire-insurance
  hotline: +65 9695 1338
  surfaces: []
plan_tiers: []
authority:
- raw/web/www.etiqa.com.sg/2026-08-25/personal-fire-insurance.md
version_in_force: '2026'
effective_from: '2026-08-25'
links:
  benefits: product/general/fire-insurance/benefits
  exclusions: product/general/fire-insurance/exclusions
  concepts: []
compiled_from_commit: working-tree
compiled_at: '2026-08-26T00:00:00'
reviewed_by:
- UNREVIEWED-eval-only
review_due: '2026-11-24'
confidence: medium
---

## What this plan is

Appointed Insurer for the HDB Fire Insurance Scheme [src:raw/web/www.etiqa.com.sg/2026-08-25/personal-fire-insurance.md#body].

Cover, limits and exclusions are identical on every channel; a channel is a route to market rather than a separate product [src:raw/web/www.etiqa.com.sg/2026-08-25/personal-fire-insurance.md#body].

## Headline benefits

The premium for 5 year term includes limit for the plan tier held is {{table:premium_for_5_year_term_includes.limit}} [src:raw/web/www.etiqa.com.sg/2026-08-25/personal-fire-insurance.md#what-is-covered].

Full benefit detail is on the [benefits page](./fire-insurance/benefits.md).

## What is not covered

The complete list is on the [exclusions page](./fire-insurance/exclusions.md).

## How to buy

<!-- okf:channel-variant -->
| Channel | Route | Contact |
|---|---|---|
| Direct (direct online) | {{channel.direct.landing}} | {{channel.direct.hotline}} |
<!-- /okf:channel-variant -->
