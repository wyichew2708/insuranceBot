---
okf_version: '0.1'
id: product/general/travel-pass
title: Travel Pass
type: product
status: approved
lifecycle: on_sale
underwriter: Etiqa Insurance Pte. Ltd.
uen: 201331905K
jurisdiction: SG
line_of_business: general
regulated_advice: false
aliases: []
channels:
- ref: channel/direct
  name: Direct
  purchase: direct_online
  landing: https://www.tiq.com.sg/travel-pass
  surfaces: []
plan_tiers: []
authority:
- raw/web/www.tiq.com.sg/2026-08-25/travel-pass.md
version_in_force: '2026'
effective_from: '2026-08-25'
links:
  exclusions: product/general/travel-pass/exclusions
  concepts: []
compiled_from_commit: working-tree
compiled_at: '2026-08-27T00:00:00'
reviewed_by:
- UNREVIEWED-eval-only
review_due: '2026-11-25'
confidence: medium
---

## What this plan is

Travel Pass is where travel meets perks [src:raw/web/www.tiq.com.sg/2026-08-25/travel-pass.md#body].

Cover, limits and exclusions are identical on every channel; a channel is a route to market rather than a separate product [src:raw/web/www.tiq.com.sg/2026-08-25/travel-pass.md#body].

## What is not covered

The complete list is on the [exclusions page](./travel-pass/exclusions.md).

## How to buy

<!-- okf:channel-variant -->
| Channel | Route | Contact |
|---|---|---|
| Direct (direct online) | {{channel.direct.landing}} | {{channel.direct.hotline}} |
<!-- /okf:channel-variant -->
