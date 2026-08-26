---
okf_version: '0.1'
id: product/protection/whole-life-insurance
title: Whole Life Insurance
type: product
status: approved
lifecycle: on_sale
underwriter: Etiqa Insurance Pte. Ltd.
uen: 201331905K
jurisdiction: SG
line_of_business: protection
regulated_advice: false
aliases:
- whole life
channels:
- ref: channel/direct
  name: Direct
  purchase: direct_online
  landing: https://www.tiq.com.sg/product/whole-life-insurance
  hotline: +65 6887 8777
  surfaces: []
plan_tiers: []
authority:
- raw/web/www.tiq.com.sg/2026-08-25/product-whole-life-insurance.md
version_in_force: '2026'
effective_from: '2026-08-25'
links:
  exclusions: product/protection/whole-life-insurance/exclusions
  concepts:
  - concept/nomination
compiled_from_commit: working-tree
compiled_at: '2026-08-27T00:00:00'
reviewed_by:
- UNREVIEWED-eval-only
review_due: '2026-11-25'
confidence: medium
---

## What this plan is

Cover, limits and exclusions are identical on every channel; a channel is a route to market rather than a separate product [src:raw/web/www.tiq.com.sg/2026-08-25/product-whole-life-insurance.md#body].

## What is not covered

The complete list is on the [exclusions page](./whole-life-insurance/exclusions.md).

## How to buy

<!-- okf:channel-variant -->
| Channel | Route | Contact |
|---|---|---|
| Direct (direct online) | {{channel.direct.landing}} | {{channel.direct.hotline}} |
<!-- /okf:channel-variant -->
