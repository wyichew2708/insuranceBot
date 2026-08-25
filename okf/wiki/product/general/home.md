---
okf_version: "0.1"
id: product/general/home
title: Home Insurance
type: product
status: approved
lifecycle: on_sale
underwriter: Etiqa Insurance Pte. Ltd.
uen: "201331905K"
jurisdiction: SG
line_of_business: general
regulated_advice: false
aliases: ["Tiq Home", "home contents", "house insurance", "HDB fire", "home cover"]
channels:
  - ref: channel/direct
    name: Direct
    purchase: direct_online
    landing: https://www.etiqa.com.sg/personal/home-insurance/
    hotline: "+65 6336 0477"
    surfaces:
      - https://www.tiq.com.sg/product/home-insurance/
  - ref: channel/agency
    name: Agency
    purchase: via_tied_agent
    landing: https://www.etiqa.com.sg/find-an-agent/
    hotline: "+65 6336 0477"
authority:
  - raw/product-summaries/home-2026.1.md
version_in_force: "2026.1"
effective_from: 2026-01-01
links:
  exclusions: product/general/home/exclusions
  concepts: [concept/excess]
compiled_from_commit: seed
reviewed_by: ["product-owner:fixture", "compliance:fixture"]
review_due: 2026-11-18
confidence: high
---

## What this plan is

Home Insurance covers household contents and alternative accommodation where the
home becomes uninhabitable after an insured peril [src:raw/product-summaries/home-2026.1.md#contents].

The policy is underwritten by Etiqa Insurance Pte. Ltd.; the route a customer
buys through is a distribution channel, not a different insurer or a different
product [src:raw/product-summaries/home-2026.1.md#contents].

## Headline benefits

Contents are covered up to {{table:contents.limit}}, and an excess of
{{table:contents.excess}} applies to each claim [src:raw/product-summaries/home-2026.1.md#excess].

Alternative accommodation is covered up to
{{table:alternative_accommodation.limit}} [src:raw/product-summaries/home-2026.1.md#accom].

## How to buy

<!-- okf:channel-variant -->
| Channel | Route | Contact |
|---|---|---|
| Direct (direct online) | {{channel.direct.landing}} | {{channel.direct.hotline}} |
| Agency (via tied agent) | {{channel.agency.landing}} | {{channel.agency.hotline}} |
<!-- /okf:channel-variant -->
