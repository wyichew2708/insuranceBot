---
okf_version: "0.1"
id: channel/direct
title: Direct channel (Singapore)
type: channel
status: approved
jurisdiction: SG
aliases: ["Direct", "direct", "buy online", "online", "Tiq", "tiq", "Etiqa online", "www.etiqa.com.sg", "www.tiq.com.sg"]
authority: [raw/web/etiqa-sg/2026-08-18-travel.md, raw/web/tiq-sg/2026-08-18-travel.md]
effective_from: 2026-01-01
compiled_from_commit: seed
reviewed_by: ["product-owner:fixture", "compliance:fixture"]
review_due: 2026-11-18
confidence: high
purchase: direct_online
landing: https://www.etiqa.com.sg/
surfaces:
  - https://www.tiq.com.sg/
hotline: "+65 6336 0477"
---

## How to reach us

<!-- okf:channel-variant -->
| Route | Contact |
|---|---|
| {{channel.direct.landing}} | {{channel.direct.hotline}} |
| https://www.tiq.com.sg/ | +65 6887 8777 |
<!-- /okf:channel-variant -->

Both addresses are front doors of this one channel, not separate insurers and
not separate products [src:raw/web/etiqa-sg/2026-08-18-travel.md].

A customer does not have to know which address they arrived through: the same
cover is quoted, sold and serviced either way, so an answer starts from the
product rather than from the address [src:raw/web/tiq-sg/2026-08-18-travel.md].

## Channel binding

This channel is a route to market for Etiqa Insurance Pte. Ltd., and the
products sold through it are the same canonical products [src:raw/web/etiqa-sg/2026-08-18-travel.md].

The purchase route and the people the customer deals with are the only
attributes that vary by channel; cover, limits and exclusions do not
[src:raw/web/etiqa-sg/2026-08-18-travel.md].
