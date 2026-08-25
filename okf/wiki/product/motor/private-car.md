---
okf_version: "0.1"
id: product/motor/private-car
title: Private Car Insurance
type: product
status: approved
lifecycle: on_sale
underwriter: Etiqa Insurance Pte. Ltd.
uen: "201331905K"
jurisdiction: SG
line_of_business: motor
regulated_advice: false
aliases: ["car insurance", "motor insurance", "private car", "vehicle cover"]
channels:
  - ref: channel/direct
    name: Direct
    purchase: direct_online
    landing: https://www.etiqa.com.sg/personal/car-insurance/
    hotline: "+65 6336 0477"
authority:
  - raw/product-summaries/private-car-2026.1.md
version_in_force: "2026.1"
effective_from: 2026-01-01
links:
  exclusions: product/motor/private-car/exclusions
  concepts: [concept/excess]
compiled_from_commit: seed
reviewed_by: ["product-owner:fixture", "compliance:fixture"]
review_due: 2026-11-18
confidence: high
---

## What this plan is

Private Car Insurance covers own damage, third-party liability and theft for a
privately registered vehicle [src:raw/product-summaries/private-car-2026.1.md#excess].

The policy is underwritten by Etiqa Insurance Pte. Ltd.; the brand a customer
buys through is a distribution surface, not a different insurer
[src:raw/product-summaries/private-car-2026.1.md#excess].

## Headline benefits

A standard own-damage excess of {{table:own_damage.excess}} applies to each claim
[src:raw/product-summaries/private-car-2026.1.md#excess].

No-claim discount accrues annually to a maximum of {{table:ncd.max_percentage}}
[src:raw/product-summaries/private-car-2026.1.md#ncd].

## How to buy

<!-- okf:channel-variant -->
| Channel | Route | Contact |
|---|---|---|
| Direct (direct online) | {{channel.direct.landing}} | {{channel.direct.hotline}} |
<!-- /okf:channel-variant -->
