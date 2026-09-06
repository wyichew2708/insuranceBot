# v2.7 — A direction is delivered, a handoff is a person

One decision from v2.5, reversed where it was wrong, and a names rule.

## What was true before

`DESIGN-v2.5.md` made every guidance reply a handoff: *"Every guide is a
handoff with steps."* Measured against the conversation dataset's
`product_fact` contract — a cited product and no handoff — that one flag was
117 of the 150 failures left after v2.6. The reply to *"Where can I find the
policy wording for Tiq 3 Plus Critical Illness?"* named the product, linked
the wording and the product summary, and said where the customer's own
schedule sits; it was the answer, and it was scored as a refusal because it
said so of itself.

## What changes

### 1. The flag follows the topic — `api.guidance.Guide.handoff`

A guide is a **handoff** where the answer lives with a person or on the
customer's own record: a quote, a refund, a payment, the policy record, an
application in progress, a claim's progress, a complaint, an offer the pages
do not carry. A guide is a **delivered direction** where the steps are the
answer: where the wording is and how to download it, how to cancel, how to
renew, how to make the claim, how to check eligibility, how to buy, and the
generic pointer to the plan's page. The product owner's rule for those —
*"guide the user to get the real answer themselves"* — describes an answer,
not a referral. The `guidance` flag stays on every guide, for the
answerability gate and the trace.

### 2. The generated suite's gap contract — `evalgen.schema.Expectation.expect_guided`

A gap probe (a claim, renewal, document or eligibility question on a product
whose pages do not carry it) used to expect `handoff=True`. Under the owner's
rule the honest reply to a gap is the steps, so the contract is now *guided*:
a handoff or a guidance reply, and never a substantive answer composed from a
page that does not hold it. The out-of-scope and injection probes keep
`expect_handoff`. Eleven recorded findings on buy, free-look and renewal
probes pass on the guidance reply and are retired from `known-findings.json`.

### 3. A title outranks another product's alias — `okf.names`

*"Does Business Owners Super Suite include work injury compensation?"* named
two products to the index: the suite by its title and Casualty Insurance by
an alias that is really a benefit. The customer typed one product's full
name, and that is the product. Two titles remain two products.

### 4. Three desks the first full run found

An offer question the corpus cannot settle goes to the promotions page, the
portal and a person. *"I lost my policy document"* and *"send me my own
policy document"* are about the customer's record, not the published
wording. *"How much will I get back if I cancel?"* asks for a refund in the
same breath as the cancellation and reads as payment, as the follow-up form
already did.

## Measured

Full conversation suite on `okf-real`, deterministic composer, 1,711 cases,
against v2.6's 1561/1711.

```
                                  v2.6                v2.7
overall                      1561/1711  91.2%    1675/1711  97.9%
  whole conversations         293/355   82.5%     333/355   93.8%
  turns                      1298/1373  94.5%    1345/1373  98.0%
  context-dependent turns     762/810   94.1%     794/810   98.0%
  product_fact               1016/1092  93.0%    1090/1092  99.8%
  handoff contract            174/179   97.2%     174/179   97.2%
  cancel journey               97/116   83.6%     116/116  100.0%
  renew journey                48/60    80.0%      57/60    95.0%
owed a handoff, did not give one      5                  5
```

Case by case: **114 gained, 0 lost.** 932 tests, mypy and ruff clean. Seed
gate 98/130, golden 9/9, nothing newly failing against the base.

### What remains

36 cases. A dozen are conversation follow-ups the contract marks as handoffs
— *"how much will it be?"* in a renewal season, *"and my phone number"* after
a change of address, *"why?"* after a rejected claim, *"what about CPF?"* —
each a reading that needs the turn before it. Three *"I am diabetic"* openers
and three *"which plan covers…"* questions expect a clarification. Two entity
turns want the underwriter named. The weather, an injection phrased without
*"previous"*, and a guarantee are shapes the deterministic guard does not
read. Five wrong-product turns remain, three of them a hospital admission
with no line word in it.
