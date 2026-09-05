# v2.6 — An incident names a line, a price with no plan is the quote steps

The remainder of the plan in `DESIGN-v2.5.md`, built against the 192 cases
that survived it.

## What changes

### 1. An incident is a guess at its line — `api.router._incident_family`

*"The airline lost my suitcase in Tokyo"* names no plan and was answered from
whichever page a lexical tie sorted first — the cyber product's exclusions,
because they mention loss. It does name a line. An incident — a subject and a
past-tense event: *"I fell"*, *"the airline lost"*, *"someone broke into"* —
with no product named or carried is read against a small lexicon of lines
(injury, travel, home, motor, maid, pet), resolved against product **titles**
so a plan added to the catalogue joins its line without a code change, and
routed as a **guess** with the line's plans as the options. The rule for a
guess is to ask, and the clarification names the whole line rather than the
first three. Injury is read before motor: *"fell off my bike and broke my
wrist"* is a personal-accident matter. A line with one plan is inferred and
scoped.

What an incident is not: *"Does Business Owners Super Suite include work
injury compensation?"* has an injury in it and is a coverage question. The
first cut read it as an incident; the second requires the subject and the
event.

### 2. A price with no plan is the quote steps

*"Get me a quote"* and *"how much does insurance cost for a family of four?"*
were asked to choose between Home and Cyber. The price of any plan is not in
the corpus and the steps to a quote are the same for all, so a price question
that resolves no product is the quote guidance before any clarification.

### 3. A draft the output screen refuses is replaced, not trimmed

The output guardrail blocked drafts carrying an external link (Business
Owners Super Suite) or a leaked clause (Premier Solutions), and the turn fell
to a bare handoff. `guardrail-output` joins the soft set: the topic's steps
are given instead. It never joins the trim — a draft the screen refused is
not reshaped.

### 4. Shapes the v2.5 run showed still answered from pages

Fraud about a record (*"someone has used my policy without my permission"*)
gets a report line, not the suspicious-message line, and goes to a person.
The medical-emergency reply is a handoff, and the jurisdiction's emergency
numbers are bound by construction in the numeric gate. Quote phrasings, fees
and GST, what moves a premium; the app and the bot; data retention; the
nearest panel clinic; external schemes; application, renewal and lapse
status; people added to a group policy — each classifies to the desk that
holds the answer.

## Measured

Full conversation suite on `okf-real`, deterministic composer, 1,711 cases,
against v2.5's 1519/1711.

```
                                  v2.5                v2.6
overall                      1519/1711  88.8%    1561/1711  91.2%
  whole conversations         278/355   78.3%     293/355   82.5%
  turns                      1281/1373  93.3%    1298/1373  94.5%
  opener turns                318/355   89.6%     333/355   93.8%
  product_fact               1016/1092  93.0%    1016/1092  93.0%
  handoff contract            149/179   83.2%     174/179   97.2%
  quote journey               114/125   91.2%     125/125  100.0%
  claim journey               319/357   89.4%     334/357   93.6%
owed a handoff, did not give one     32                  5
```

Case by case: **42 gained, 0 lost.** Tests, mypy and ruff clean. Seed gate
98/130, golden 9/9, nothing newly failing against the base. Four recorded
findings — the baggage-loss scenarios in `known-findings.json` — now pass and
are retired; the three medical-expenses phrasings remain.

### What the first full run found

Five losses, three patterns, all narrowed in the second cut: an incident
needs a subject and a past-tense event; only *"where is the nearest panel
clinic"* is a where-is question, *"does it have to be a panel hospital"* is
answered by the pages; *"will I need a medical examination for Term Life"* is
a product question and *"why do I need one"* is about the customer's own
application.

One reading left as it was: *"Does Business Owners Super Suite include work
injury compensation?"* names two products to the name index — the suite by
title and Casualty Insurance by its alias *"work injury compensation"* — and
is therefore ambiguous. The case passes on open retrieval. A title should
outrank a benefit-shaped alias; that is a names change for another day.

### What remains

150 failures. 119 are turns guided where the dataset expected an answer the
pages do not hold — document requests 28/46, claim steps 10/112, renewal
9/43, eligibility 13/148 — and the guidance is the right reply until the page
exists. 19 are answers where a handoff was owed, most inside conversations
whose later turns the contract marks as handoffs. 5 are wrong-product turns
and 7 are directory and entity misses.
