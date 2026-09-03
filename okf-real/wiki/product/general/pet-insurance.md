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
- cat insurance
- dog insurance
- pet
- pet cover
- pet insurance
- tiq pet
- tiq pet insurance
channels:
- ref: channel/direct
  name: Direct
  purchase: direct_online
  landing: https://www.etiqa.com.sg/personal/pet-insurance
  surfaces:
  - https://www.tiq.com.sg/product/pet-insurance
plan_tiers:
- pawmazing-get-quote
- pawtastic-get-quote
- pawfect-get-quote
authority:
- raw/web/www.etiqa.com.sg/2026-08-25/personal-pet-insurance.md
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

Your pets are cherished members of your family, and their health and wellbeing deserve the utmost protection [src:raw/web/www.etiqa.com.sg/2026-08-25/personal-pet-insurance.md#body].

## What it covers

Unmatched Protection: Industry-leading coverage for surgical and non-surgical procedures, accidental injuries, congenital, and hereditary conditions [src:raw/web/www.etiqa.com.sg/2026-08-25/personal-pet-insurance.md#unmatched-protection].

Discounts and Benefit: No-claim discounts, multi-pet discounts, and additional coverage for final expenses [src:raw/web/www.etiqa.com.sg/2026-08-25/personal-pet-insurance.md#discounts-and-benefit].

The policy wording sets out cover under: Death Benefit; Terminal Illness (TI) Benefit; Surrender Benefit; Partial Withdrawal Benefit; Maturity Benefit [src:raw/wordings/dash-pet-plus-policy-contract-v3-26-02042026.md].

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
