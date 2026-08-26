---
okf_version: '0.1'
id: product/motor/private-car-insurance
title: Car Insurance
type: product
status: approved
lifecycle: on_sale
underwriter: Etiqa Insurance Pte. Ltd.
uen: 201331905K
jurisdiction: SG
line_of_business: motor
regulated_advice: false
aliases:
- car
channels:
- ref: channel/direct
  name: Direct
  purchase: direct_online
  landing: https://www.tiq.com.sg/product/private-car-insurance
  hotline: +65 6311 4128
  surfaces: []
plan_tiers: []
authority:
- raw/web/www.tiq.com.sg/2026-08-25/product-private-car-insurance.md
version_in_force: '2026'
effective_from: '2026-08-25'
links:
  exclusions: product/motor/private-car-insurance/exclusions
  concepts:
  - concept/excess
compiled_from_commit: working-tree
compiled_at: '2026-08-27T00:00:00'
reviewed_by:
- UNREVIEWED-eval-only
review_due: '2026-11-25'
confidence: medium
---

## What this plan is

Not driving much these days? No problem [src:raw/web/www.tiq.com.sg/2026-08-25/product-private-car-insurance.md#why-private-car-insurance].

Cover, limits and exclusions are identical on every channel; a channel is a route to market rather than a separate product [src:raw/web/www.tiq.com.sg/2026-08-25/product-private-car-insurance.md#body].

## What is not covered

The complete list is on the [exclusions page](./private-car-insurance/exclusions.md).

## How to buy

<!-- okf:channel-variant -->
| Channel | Route | Contact |
|---|---|---|
| Direct (direct online) | {{channel.direct.landing}} | {{channel.direct.hotline}} |
<!-- /okf:channel-variant -->
