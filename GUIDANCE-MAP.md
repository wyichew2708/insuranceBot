# Guidance map — what the assistant can be asked, and where it leads

Generated from `okf-real` by `scripts/guidance_map.py` (`make guidance-map`);
regenerate rather than edit. The reference for the greeting map, the browse tree and
the follow-on chips in `DESIGN-v2.9.md` §3 (A2). Every node is one of three things:

| Mark | Node | What tapping it does |
|---|---|---|
| ✎ | a question the corpus answers | sends that exact question; the reply is delivered, by construction |
| ↗ | a destination | opens a registry address (portal, claims, renewal, promotions, contact) |
| ◌ | a topic the pages do not hold for this plan | not shown as a chip; the reply falls to the steps |

No node is model-written, none carries a digit, and a ✎ node is offered only while the
page behind it is approved and in its effective window.

## The greeting

> Hi. I answer from Etiqa's policy wordings and product pages, and I'll point you to the right place for anything about your own policy. Pick a branch or just ask.

Then six branches. The tree below is the full expansion.

## Level 1 — the six branches

```
Etiqa assistant
├─ Product information    what a plan covers, excludes, needs to claim on, who can buy it
├─ Claims                 how to claim, what to send, where to track it
├─ My policy              log in, renew, update, cancel — the customer's own record
├─ Buy and quote          the lines sold, how to buy, where a price comes from
├─ Promotions             live offers only
└─ Help and contact       a person, a scam report, app and login help
```

## Branch 1 — Product information

37 approved plans across 8 lines. Line → plan → the topic ring. The ring has
the same shape for every plan; a topic appears only where the pages hold it (see the matrix).

```
Product information
├─ General
│   ├─ Personal
│   │   ├─ Enrich aspire VII
│   │   ├─ ePROTECT safety
│   │   ├─ HDB Fire Insurance
│   │   ├─ Personal Cyber Insurance
│   │   ├─ Pet Insurance
│   │   ├─ Tiq CashSaver
│   │   ├─ Tiq Home Insurance
│   │   ├─ Tiq Maid Insurance
│   │   ├─ Tiq Personal Accident
│   │   ├─ Tiq Travel Insurance
│   │   ├─ Travel Infinite
│   │   ├─ Travel Takaful
│   └─ Commercial
│       ├─ Casualty Insurance
│       ├─ Engineering Insurance
│       ├─ Marine Insurance
│       ├─ Miscellaneous Insurance
│       ├─ Property Insurance
├─ Motor
│   ├─ Commercial Vehicle Insurance
│   ├─ Motorcycle Insurance
│   ├─ Private Car Insurance
├─ Life & protection
│   ├─ Cancer Insurance
│   ├─ DIRECT – Etiqa term life II
│   ├─ DIRECT – Etiqa whole life
│   ├─ ePROTECT term life
│   ├─ Term Life Insurance
│   ├─ Tiq 3 Plus Critical Illness
│   ├─ Whole Life Insurance
├─ Health & medical
│   ├─ Accident & Health Insurance
├─ Savings & retirement
│   ├─ eEASY savepro
│   ├─ Tiq 3-Year Endowment Plan
│   ├─ Tiq Easy Save
├─ Investments
│   ├─ Invest Smart Vista
│   ├─ Invest vista
│   ├─ Tiq Invest
├─ Business
│   ├─ Business Owners Super Suite
│   ├─ Corporate Travel Insurance
├─ Premier
│   ├─ Premier Solutions
├─ Tell me what happened          an incident names its line; the plans in that line are offered
│     ✎ my flight was delayed · ✎ the airline lost my bag · ✎ someone broke into my flat
│     ✎ my helper is unwell · ✎ i had a car accident · ✎ my dog needs the vet
└─ Insurance terms
      ✎ What does commencement date mean?
      ✎ What does excess mean?
      ✎ What does nomination mean?
      ✎ What does policy schedule mean?
```

### The topic ring (every plan)

```
<Plan>
  ✎ What does <Plan> cover?                              needs: always
  ✎ What does <Plan> not cover?                          needs: exclusions page
  ✎ What are the cover limits for <Plan>?                needs: benefits or cover page
  ✎ Who can buy <Plan>?                                  needs: eligibility or FAQ page
  ✎ How do I buy <Plan>?                                 needs: a channel binding
  ✎ Is there a promotion for <Plan>?                     needs: a live promotion page
  ✎ How do I make a claim on <Plan>?                     needs: claims page or claim journey
  ✎ What documents do I need to claim on <Plan>?         needs: claims page or claim journey
  ✎ What do the terms in <Plan> mean?                    needs: definitions page
  ✎ How do I cancel or renew <Plan>?                     needs: conditions page
  ✎ Compare <Plan> with <another plan>                   compare intent (A4); a bound table
```

After any ring answer the section chips of that page follow ("<heading> — <Plan>"), then
a "back to <line>" chip.

## Branch 2 — Claims

```
Claims
├─ ✎ How do I make a claim?                 → asks which plan, then that plan's steps
├─ ✎ What documents does a claim need?       → same, then the documents section
├─ ✎ How long does a claim take?             → only where a page states it; else the claims desk
├─ ↗ Track my claim                          Customer portal
├─ ↗ Submit a claim                          Claims and services
└─ Claim journeys the corpus holds:
      journey/claim/accident-health
      journey/claim/casualty
      journey/claim/fire-insurance
      journey/claim/miscellaneous
```

## Branch 3 — My policy

Destination-led by design: nothing here is in a policy document. The steps come from the
guidance table; the addresses from the registry.

```
My policy
├─ ↗ Log in and view my policy               Customer portal
├─ ↗ Renew online                            Online renewal  (general insurance only)
├─ Update my details                         steps → portal, then a person
│     nominee · address · contact · bank · add or remove a driver or dependant
├─ Cancel, refund, payments                  steps → portal, then a person
│     cancel · free-look · refund · pay by GIRO or card · premium due
├─ ✎ Where is the policy wording for <Plan>?     corpus: links the published document
└─ ✎ What is a policy schedule?              concept page
```

## Branch 4 — Buy and quote

```
Buy and quote
├─ ✎ Which kinds of insurance do you sell?   the lines, one example each
├─ ✎ I need <line> insurance                 the plans in that line
├─ ✎ How do I buy <Plan>?                    the channel route: online, agent, broker
├─ ↗ Get a quote                             the plan's own page; "the price depends on your details"
└─ ↗ Speak to an adviser                     a recommendation is a licensed adviser's call
```

## Branch 5 — Promotions

63 promotion pages compiled; only those inside their validity window are offered.

```
Promotions
├─ ✎ Is there a promotion for <Plan>?        for the plans in the matrix with an offer
└─ ↗ All current promotions                  Promotions
```

## Branch 6 — Help and contact

```
Help and contact
├─ ↗ Talk to a person                        Contact us
├─ ↗ Report a scam or suspicious message     a person only, never the portal
├─ ↗ App and portal help                     login · OTP · password → portal, then a person
├─ ✎ Who underwrites these policies?         entity page
└─ ✎ What can you help with?                 the capability reply
```

## Along the way — what to offer after each answer

Chips follow the customer's journey, not only the last intent, and a topic already answered
in this session is not offered again.

| After the customer asked | Offer next, in order | Why this order |
|---|---|---|
| a bare plan name (overview) | not covered · limits · who can buy · how to buy | evaluate, then apply |
| what it covers | not covered · limits · how to claim · promotion | the exclusions belong beside the cover |
| what it does not cover | what it covers · how to claim · how to buy | back to the positive, then forward |
| the limits | not covered · who can buy · how to buy · compare | a figure question is a buying question |
| who can buy | how to buy · promotion · what it covers | eligibility, then apply |
| how to buy | promotion · get a quote ↗ · what it covers | apply, then price |
| a promotion | how to buy · what it covers · not covered | offer, then apply |
| how to claim | documents needed · track my claim ↗ · not covered | claim, evidence, status |
| a claim-status or servicing handoff | documents needed · what it covers · talk to a person ↗ | stay in the conversation after a handoff |
| a price handoff | how to buy · what it covers · limits | the quote is one tap away; the plan is still here |
| a definition | what it covers · not covered | back to the product |
| a clarification (which plan?) | the candidate plans · “I'm not sure which plan I have” ↗ | the options are the chips |
| off-topic, or a greeting | the six branches | the map |

## Rules

1. Nodes are generated from the bundle at load time, never written into code.
2. A ✎ node exists only if the page that answers it is approved and effective today.
3. No digits anywhere in a node; a number in an answer must bind to a row.
4. ↗ addresses come from the destination registry, never from retrieved text.
5. The map rides on the greeting envelope as a typed `map` field: each node carries `kind`
   (`question` | `destination`), `label`, and `question` or `url`; children nest. A plain
   client flattens it to chips.
6. The map is shown once. Per-turn chips take over; a "back to the start" chip returns.
7. Proved by a generated suite that asks every chip the map and the per-turn table offer and
   asserts each reply is delivered; and by two report rows, dead-end rate and chip coverage.

## Coverage matrix — which ring topics each plan can answer

✓ the pages hold it · they do not (the chip is omitted). *Bound rows* counts benefit-table
rows, which is what lets a limit answer carry a figure; a plan with none still answers a limit
question with the wording and no number.

| Line | Plan | coverage | exclusion | limit | eligibility | application | offer | claim | documents | definition | conditions | bound rows |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| General | Casualty Insurance | ✓ | ✓ | ✓ | · | ✓ | · | ✓ | ✓ | ✓ | ✓ | · |
| General | Engineering Insurance | ✓ | ✓ | ✓ | · | ✓ | · | · | · | · | ✓ | · |
| General | Enrich aspire VII | ✓ | ✓ | ✓ | · | ✓ | · | ✓ | ✓ | ✓ | ✓ | · |
| General | ePROTECT safety | ✓ | ✓ | · | ✓ | · | ✓ | ✓ | ✓ | ✓ | ✓ | · |
| General | HDB Fire Insurance | ✓ | ✓ | ✓ | · | ✓ | · | ✓ | ✓ | · | · | 12 |
| General | Marine Insurance | ✓ | ✓ | · | · | ✓ | · | ✓ | ✓ | · | ✓ | · |
| General | Miscellaneous Insurance | ✓ | ✓ | · | · | ✓ | · | ✓ | ✓ | ✓ | ✓ | · |
| General | Personal Cyber Insurance | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | · |
| General | Pet Insurance | ✓ | ✓ | ✓ | ✓ | ✓ | · | ✓ | ✓ | ✓ | ✓ | 3 |
| General | Property Insurance | ✓ | ✓ | ✓ | · | ✓ | · | ✓ | ✓ | ✓ | ✓ | · |
| General | Tiq CashSaver | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | · |
| General | Tiq Home Insurance | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | · |
| General | Tiq Maid Insurance | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | 7 |
| General | Tiq Personal Accident | ✓ | ✓ | ✓ | ✓ | ✓ | · | ✓ | ✓ | ✓ | ✓ | · |
| General | Tiq Travel Insurance | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | 6 |
| General | Travel Infinite | ✓ | ✓ | ✓ | ✓ | ✓ | · | ✓ | ✓ | ✓ | ✓ | 15 |
| General | Travel Takaful | ✓ | ✓ | · | · | ✓ | · | · | · | · | ✓ | · |
| Motor | Commercial Vehicle Insurance | ✓ | ✓ | ✓ | · | ✓ | · | ✓ | ✓ | ✓ | ✓ | · |
| Motor | Motorcycle Insurance | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | · | ✓ | · |
| Motor | Private Car Insurance | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | · |
| Life & protection | Cancer Insurance | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | · |
| Life & protection | DIRECT – Etiqa term life II | ✓ | ✓ | · | · | · | · | ✓ | ✓ | ✓ | ✓ | · |
| Life & protection | DIRECT – Etiqa whole life | ✓ | ✓ | · | · | · | · | · | · | · | ✓ | · |
| Life & protection | ePROTECT term life | ✓ | ✓ | · | · | · | · | ✓ | ✓ | ✓ | ✓ | · |
| Life & protection | Term Life Insurance | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | · |
| Life & protection | Tiq 3 Plus Critical Illness | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | · |
| Life & protection | Whole Life Insurance | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | · |
| Health & medical | Accident & Health Insurance | ✓ | ✓ | ✓ | ✓ | ✓ | · | ✓ | ✓ | ✓ | ✓ | · |
| Savings & retirement | eEASY savepro | ✓ | ✓ | ✓ | ✓ | ✓ | · | · | · | ✓ | ✓ | · |
| Savings & retirement | Tiq 3-Year Endowment Plan | ✓ | ✓ | ✓ | ✓ | ✓ | · | ✓ | ✓ | ✓ | ✓ | · |
| Savings & retirement | Tiq Easy Save | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | · |
| Investments | Invest Smart Vista | ✓ | ✓ | ✓ | · | ✓ | · | ✓ | ✓ | ✓ | ✓ | · |
| Investments | Invest vista | ✓ | ✓ | ✓ | · | ✓ | · | ✓ | ✓ | ✓ | ✓ | 3 |
| Investments | Tiq Invest | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | · |
| Business | Business Owners Super Suite | ✓ | ✓ | ✓ | · | ✓ | · | ✓ | ✓ | ✓ | ✓ | · |
| Business | Corporate Travel Insurance | ✓ | ✓ | · | · | ✓ | · | · | · | · | · | · |
| Premier | Premier Solutions | ✓ | ✓ | · | · | ✓ | · | · | · | · | ✓ | · |

## Content gaps the map makes visible

- **No route to buy** on 4 plans (ePROTECT safety, DIRECT – Etiqa term life II, DIRECT – Etiqa whole life, ePROTECT term life): the how-to-buy
  chip is missing and the application intent falls to the apply steps. A channel binding on
  each page closes it.
- **No claim steps** on 6 plans (Engineering Insurance, Travel Takaful, DIRECT – Etiqa whole life, eEASY savepro, Corporate Travel Insurance, Premier Solutions): the claims chip
  is missing and the reply is the claims desk. A claims section or a claim journey page closes it.
- **No eligibility page or FAQ** on 17 plans: who-can-buy falls to the
  eligibility steps. The eligibility table in `DESIGN-v2.9.md` §3 (C2) closes it with bound ages.
- **Benefit-table rows on 6 of 37 plans** (HDB Fire Insurance, Pet Insurance, Tiq Maid Insurance, Tiq Travel Insurance, Travel Infinite, Invest vista):
  everywhere else a limit answer is the wording with its figures trimmed. The single largest
  content gap, and a document-extraction problem rather than a retrieval one.
- **Policy servicing is uncompiled.** The crawled policy-services page is not a journey, so the
  My policy branch is desk-led. Compiling it into servicing journeys makes those steps corpus-backed.
- **The app has no corpus content.** App and portal help is two buttons until its pages are compiled.

