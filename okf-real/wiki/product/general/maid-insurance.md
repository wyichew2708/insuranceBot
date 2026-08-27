---
okf_version: '0.1'
id: product/general/maid-insurance
title: Maid Insurance
type: product
status: approved
lifecycle: on_sale
underwriter: Etiqa Insurance Pte. Ltd.
uen: 201331905K
jurisdiction: SG
line_of_business: general
regulated_advice: false
aliases:
- maid
channels:
- ref: channel/direct
  name: Direct
  purchase: direct_online
  landing: https://www.etiqa.com.sg/personal/maid-insurance
  hotline: +65 6887 8777
  surfaces:
  - https://www.tiq.com.sg/product/maid-insurance
plan_tiers:
- plan-a
- plan-b
- plan-c
authority:
- raw/web/www.etiqa.com.sg/2026-08-25/personal-maid-insurance.md
- raw/web/www.tiq.com.sg/2026-08-25/product-maid-insurance.md
version_in_force: '2026'
effective_from: '2026-08-25'
links:
  benefits: product/general/maid-insurance/benefits
  exclusions: product/general/maid-insurance/exclusions
  concepts: []
compiled_from_commit: working-tree
compiled_at: '2026-08-27T00:00:00'
reviewed_by:
- UNREVIEWED-eval-only
review_due: '2026-11-25'
confidence: high
---

## What this plan is

Your domestic helper is a key member of your household [src:raw/web/www.etiqa.com.sg/2026-08-25/personal-maid-insurance.md#body].

## Headline benefits

The accidental death limit for the plan tier held is {{table:accidental_death.limit}} [src:raw/web/www.tiq.com.sg/2026-08-25/product-maid-insurance.md#what-is-covered].

The sum insured s is {{table:sum_insured_s.period}} [src:raw/web/www.etiqa.com.sg/2026-08-25/personal-maid-insurance.md#what-is-covered].

The third party liability limit for the plan tier held is {{table:third_party_liability.limit}} [src:raw/web/www.tiq.com.sg/2026-08-25/product-maid-insurance.md#what-is-covered].

Full benefit detail is on the [benefits page](./maid-insurance/benefits.md).

## What is not covered

The complete list is on the [exclusions page](./maid-insurance/exclusions.md).

## How to buy

<!-- okf:channel-variant -->
| Channel | Route | Contact |
|---|---|---|
| Direct (direct online) | {{channel.direct.landing}} | {{channel.direct.hotline}} |
<!-- /okf:channel-variant -->
