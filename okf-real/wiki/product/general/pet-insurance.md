---
okf_version: '0.1'
id: product/general/pet-insurance
title: Pet Insurance
type: product
status: approved
lifecycle: on_sale
underwriter: Etiqa Insurance Pte. Ltd.
uen: 201331905K
jurisdiction: SG
line_of_business: general
regulated_advice: false
aliases:
- pet
- pet insurance
channels:
- ref: channel/direct
  name: Direct
  purchase: direct_online
  landing: https://www.etiqa.com.sg/personal/pet-insurance/pet-insurance
  surfaces:
  - https://www.tiq.com.sg/product/pet-insurance
plan_tiers:
- pawmazing-get-quote
- pawtastic-get-quote
- pawfect-get-quote
authority:
- raw/web/www.etiqa.com.sg/2026-08-25/personal-pet-insurance-pet-insurance.md
- raw/web/www.tiq.com.sg/2026-08-25/product-pet-insurance.md
version_in_force: '2026'
effective_from: '2026-08-25'
links:
  benefits: product/general/pet-insurance/benefits
  exclusions: product/general/pet-insurance/exclusions
  concepts:
  - concept/policy-schedule
compiled_from_commit: working-tree
compiled_at: '2026-09-03T00:00:00'
reviewed_by:
- UNREVIEWED-eval-only
review_due: '2026-12-02'
confidence: high
---

## What this plan is

Marketing Consent Terms & Conditions [src:raw/web/www.etiqa.com.sg/2026-08-25/personal-pet-insurance-pet-insurance.md#find-out-more].

## What it covers

The policy wording sets out cover under: Surgical Illness Cover; Non-Surgical Illness Cover; Accidental Injury; Funeral Expenses; Third Party Liability; Coverage for Congenital and Hereditary Conditions; Geographical Coverage; Payment of Benefit [src:raw/wordings/pet-insurance-policy-wording-v1-25-january-2024-final.md].

## Headline benefits

The n a limit for the plan tier held is {{table:n_a.limit}} [src:raw/web/www.tiq.com.sg/2026-08-25/product-pet-insurance.md#what-is-covered].

Full benefit detail is on the [benefits page](./pet-insurance/benefits.md).

## What is not covered

The complete list is on the [exclusions page](./pet-insurance/exclusions.md).

## How to buy

<!-- okf:channel-variant -->
| Channel | Route | Contact |
|---|---|---|
| Direct (direct online) | {{channel.direct.landing}} | {{channel.direct.hotline}} |
<!-- /okf:channel-variant -->
