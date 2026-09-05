# v2.4 — Scoped retrieval, a layered router, and asking when unsure

Branch `claude/create-branch-v2-3-dbfh1e`, on top of v2.3.1. Companion to
[DESIGN-v2.3.md](DESIGN-v2.3.md). The safety architecture is unchanged.

Three rules from the product owner, in their words:

1. *Retrieval must only retrieve content for the product asked about. All
   content and documents must be tagged by product; a question about
   commercial car insurance checks only commercial car insurance material.*
2. *The router should have layers — a main router, a second and a third —
   so that granular groups of mini agents handle specific queries.*
3. *Whenever information is unsure or missing, ask the customer to clarify.*

## What was true before this change

**Wiki pages were already scoped, once a product was known — but only at the
front door.** `frontmatter_filter` rejects another product's page when a focus
product is resolved. The graph walk did not: `ref` edges can land on another
product's page, and the reverse-walk rescue exists precisely to reach a product
from a concept. Both could cross products after the filter had done its job.

**Raw documents were not scoped at all.** `rag_search` walks every file under
`raw/` and admits by the marketing screen and the in-force version. Nothing in
it knows which product a wording belongs to. The field test's largest failure
class — `cited products ['travel'], expected ['travel-insurance']` — is this.

**Documents had no product tag.** The compiler matches a wording to a product
by filename at compile time and forgets the result. At answer time the
information does not exist.

**"Unsure" already had a name and was silently overridden.** `Ask.named_by`
records how the product was identified: `title` or `alias` means the customer
said it; `flagship` means the customer named a *category* ("travel insurance",
four products) and the code picked the one whose title is the category;
`history` means it was carried from an earlier turn. Flagship is a guess, and
it was answered as if it were a name.

**The router existed as branches in one function.** Smalltalk, entity,
directory, emergency, off-topic, out-of-corpus, advice, ambiguity, then
retrieval — in that order, inline in `_answer_turn`, with no record of which
branch a turn took. The evaluation could not group by it.

## What changes

### 1. Every raw document carries a product tag — `okf.scope.raw_product_index`

Built once per bundle from what already exists and cached on it, the way the
graph is:

| source | tag |
|---|---|
| `raw/wordings`, `product-summaries`, `brochures`, `faq` | the catalogue product whose `documents` keys or slug forms the filename contains; longest match wins |
| `raw/benefit-tables/<slug>.csv` | the slug |
| `raw/web/...` | the catalogue product whose `urls` match the crawl manifest's canonical URL; else a product page whose path names one slug |
| corporate pages — contact, claims-and-services, promotions, policy-services | `shared` |
| anything else | `unknown` |

A bundle with no catalogue (the seed) tags nothing, and a scope with nothing
tagged admits everything. The seed's suites score exactly as before.

### 2. A per-turn `Scope`, applied everywhere a page or document enters

`Scope.for_product(bundle, key)` admits a wiki page iff it belongs to that
product or is product-agnostic (concepts, channels, journeys, entity), and
admits a raw document iff it is tagged to that product or `shared`. It is
applied in `wiki_read`'s `take()` — so neither the walk nor the rescue can
cross products — and in `rag_search` and its dense half. Rejections are
counted on the trace. A turn with no resolved product runs open, as today.

### 3. A router with three layers — `api.router`

```
Layer 1  what kind of turn        smalltalk · emergency · off_topic · account_state ·
                                  advice · browse · entity · product
Layer 2  which product            named · carried · ambiguous · guessed · none
Layer 3  which handler            coverage · exclusions · limits · claims · eligibility ·
                                  conditions · documents · price · offer · definition ·
                                  application · general
```

The decision is made once, deterministically, right after the Ask is read,
and recorded on the trace and in the evaluation's per-turn record. The
existing branches are the layer-1 handlers; the layer-3 handlers are the
intent-driven paths the composer and gates already take (`holds_answer`,
`REQUIREMENTS`, `SHORTFALL`, the destinations). What is new is that the
decision is explicit, inspectable, and grouped in the report.

### 4. Asking when unsure

Layer 2 decides. `named` and `carried` proceed. `ambiguous` asks which of the
candidates. `guessed` — the flagship case — now asks too, listing the
category's members, instead of answering about the one whose title happened
to match. `none` on a product-specific handler (coverage, exclusions, limits,
claims, eligibility, conditions, application) asks which product; on `browse`
it lists what is sold; on `definition` it answers from the concept pages,
because "what is an excess" does not depend on the product.

`price`, `offer` and `documents` with no product stay on the routed path: the
dataset owes those a handoff with a destination, and asking first would trade
a correct handoff for a question.

Two directory gaps close on the way. *"What insurance products do you offer?"*
matched no line and fell through to a product clarification; browse with no
line now lists the lines. *"I need life insurance"* was not read as browsing;
it is.

## What this will do to the score, and why some of it is intended

- **The six situation-opener templates** (*"the airline lost my suitcase in
  Tokyo"* → must cite `travel-insurance`) assert the flagship guess. Under
  rule 3 they will be asked which travel product, and fail as written. That
  is 20 cases, and the dataset is wrong by the product owner's own rule; they
  are reported, not edited.
- **Scoping trades recall for precision by design.** A question whose product
  was resolved wrongly will now find nothing rather than something from the
  right product by luck. The measurement says whether that happens.
- **The `wrong product cited` class** (20 turns) and the field test's
  `travel` vs `travel-insurance` class should fall; the vacuous-pass problem
  in `EVALUATION.md` does not move, because it is the dataset's contract, not
  retrieval.

Acceptance: every case-level loss against 1333/1711 is either one of the 20
above or is explained, and `product_fact` does not fall.
