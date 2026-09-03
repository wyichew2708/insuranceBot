---
okf_version: '0.1'
id: product/protection/cancer-insurance
title: Cancer Insurance
type: product
status: approved
lifecycle: on_sale
underwriter: Etiqa Insurance Pte. Ltd.
uen: 201331905K
jurisdiction: SG
line_of_business: protection
regulated_advice: false
aliases:
- cancer
- cancer cover
- cancer insurance with no claim discount
- tiq cancer insurance
channels:
- ref: channel/direct
  name: Direct
  purchase: direct_online
  landing: https://www.tiq.com.sg/product/cancer-insurance
  surfaces: []
plan_tiers: []
authority:
- raw/web/www.tiq.com.sg/2026-08-25/product-cancer-insurance.md
version_in_force: '2026'
effective_from: '2026-08-25'
links:
  exclusions: product/protection/cancer-insurance/exclusions
  concepts: []
compiled_from_commit: working-tree
compiled_at: '2026-09-03T00:00:00'
reviewed_by:
- UNREVIEWED-eval-only
review_due: '2026-12-02'
confidence: medium
---

## What this plan is

Our Cancer Insurance provides coverage for all stages of cancer, including early stage cancer which critical illness insurance or rider may not provide [src:raw/web/www.tiq.com.sg/2026-08-25/product-cancer-insurance.md#frequently-asked-questions].

## What it covers

The policy wording sets out cover under: Cancer Benefit; Death Benefit; Covered Events; Monthly Payout Benefit [src:raw/wordings/cancer-insurance-policy-contract-v1-25.md].

## What is not covered

The complete list is on the [exclusions page](./cancer-insurance/exclusions.md).

## How to buy

<!-- okf:channel-variant -->
| Channel | Route | Contact |
|---|---|---|
| Direct (direct online) | {{channel.direct.landing}} | {{channel.direct.hotline}} |
<!-- /okf:channel-variant -->
