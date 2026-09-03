---
okf_version: '0.1'
id: product/general/travel-insurance
title: Travel Insurance
type: product
status: approved
lifecycle: on_sale
underwriter: Etiqa Insurance Pte. Ltd.
uen: 201331905K
jurisdiction: SG
line_of_business: general
regulated_advice: false
aliases:
- travel
channels:
- ref: channel/direct
  name: Direct
  purchase: direct_online
  landing: https://www.etiqa.com.sg/personal/travel-insurance
  surfaces:
  - https://www.tiq.com.sg/product/travel-insurance
plan_tiers:
- 1-000
- 1-000
- 3-000
authority:
- raw/web/www.etiqa.com.sg/2026-08-25/personal-travel-insurance.md
- raw/web/www.tiq.com.sg/2026-08-25/product-travel-insurance.md
version_in_force: '2026'
effective_from: '2026-08-25'
links:
  benefits: product/general/travel-insurance/benefits
  exclusions: product/general/travel-insurance/exclusions
  concepts:
  - concept/commencement-date
  - concept/excess
compiled_from_commit: working-tree
compiled_at: '2026-09-03T00:00:00'
reviewed_by:
- UNREVIEWED-eval-only
review_due: '2026-12-02'
confidence: high
---

## What this plan is

We are your travel buddy [src:raw/web/www.etiqa.com.sg/2026-08-25/personal-travel-insurance.md#body].

## Headline benefits

The child limit for the plan tier held is {{table:child.limit}} [src:raw/web/www.tiq.com.sg/2026-08-25/product-travel-insurance.md#what-is-covered].

The home content cover limit for the plan tier held is {{table:home_content_cover.limit}} [src:raw/web/www.tiq.com.sg/2026-08-25/product-travel-insurance.md#what-is-covered].

Full benefit detail is on the [benefits page](./travel-insurance/benefits.md).

## What is not covered

The complete list is on the [exclusions page](./travel-insurance/exclusions.md).

## How to buy

<!-- okf:channel-variant -->
| Channel | Route | Contact |
|---|---|---|
| Direct (direct online) | {{channel.direct.landing}} | {{channel.direct.hotline}} |
<!-- /okf:channel-variant -->
