# v2.3 — Three retrieval layers, three failure modes

Branch `claude/genai-insurance-chatbot-v2.3`, from `4af7143`. Companion to
[DESIGN-v2.1.md](DESIGN-v2.1.md) and [DESIGN-v2.2.md](DESIGN-v2.2.md), neither
of which it replaces. The safety architecture is unchanged: the model phrases,
never establishes a fact; the gates verify afterwards; every claim carries a
source ref. v2.3 is about what reaches the composer in the first place.

## Why

The retrieval pipeline had three layers on paper and one and a half in code.

| Layer | Answers | v2.2 state |
|---|---|---|
| **OKF** — the compiled wiki | "why this exists" — curated, approved, dated | complete |
| **Graph** — the typed links between pages | "what points at what" | a flattened neighbour list and a BFS |
| **Vectors** — dense recall | "the thing I can't name precisely" | pages only, wiki only |

Each closes a failure the other two cannot. Lexical ranking cannot find a page
whose words the customer does not know. Similarity cannot *guarantee* the
complete exclusion set — the guarantee is what the graph is for, and it is
the difference between an answer that is incomplete and an answer that is
wrong. And neither knows why a page exists, which is what the frontmatter is
for. The gaps below are the parts of that division of labour that were
asserted in the design and not implemented.

**Graph.** `Bundle.neighbours` returned `links.all_refs()` — a bare list of
page ids with the edge types flattened out of it. By the time the harness read
it, the exclusions edge and a passing concept reference were the same thing:
the walk could not prefer one, the trace could not say which had been
followed, and `links.claims` — typed in the schema, present in the
frontmatter — was followed by nothing that knew what it was for. Containment
(`product/general/travel/faq` belongs to `product/general/travel`) was
re-derived by string slicing in three places, one of which was a hard-coded
list of six suffixes. And every edge ran one way, so a turn that landed on
`concept/pre-existing-condition` could not get back to a product.

**Vectors.** The index is built one row per section and queried one row per
section — and the heading was dropped on arrival. `frontmatter_filter` pooled
the hits to a page score, and the composer then chose which section to answer
from on word overlap alone. `vectors.py`'s own opening note said so: *"It is
still unpatched at section retrieval."* Meanwhile the RAG fallback over `raw/`
— which fires on exactly the questions the wiki cannot answer — searched by
word overlap and nothing else.

## What changed

### 1. The graph is a graph — `packages/okf/okf/graph.py`

A `PageGraph` built once per bundle and cached on it, the way `term_idf` is.

```
EdgeKind   exclusions · benefits · claims · concept · ref · child
Edge       src → dst, kind
PageGraph  out_edges / in_edges / neighbours     typed, both directions
           owner_of / children_of                containment
           walk(start, order, max_pages)         deterministic, kind-ordered
           describe(page_id)                     for the trace and the console
```

Four things it has that the flattened list did not:

- **Kinds survive.** An edge carries its type from the frontmatter all the way
  to `LoadedPage.edge`, so a trace distinguishes a guaranteed exclusion set
  from a lucky neighbour.
- **Containment is real.** Derived from the id path and kept as a hierarchy
  (`owner_of`, `children_of`) *and* published as an `EdgeKind.child` edge. The
  real corpus has 150 child pages — `/cover`, `/definitions`, `/eligibility` —
  that no `links:` block anywhere points at. Before, the only route to one was
  for its own words to outscore everything else.
- **Edges run both ways.** The reverse index is built in the same pass.
- **Order is total.** Kind first, then the other end of the edge,
  alphabetically. Two runs over one bundle produce the same walk, which is
  what makes a trace worth reading (§F.4).

### 2. Traversal is shaped by the question — `plan_for`

`plan_for(question)` returns the six edge kinds in the order this question
wants them followed. **A permutation, never a filter**: an intent decides what
is read *first*, never what is read at all, so no phrasing can talk the
harness out of the exclusions page the completeness gate requires. A claims
question promotes `claims`; an exclusion question promotes `exclusions`; a
figure question promotes `benefits`.

`wiki_read` now runs three passes over the graph, in the order their
guarantees are worth:

1. **The product's own typed edges**, from the product a seed *belongs to* —
   all three of them, in the question's order. `claims` was in the schema and
   in the frontmatter and reached only if the generic walk had budget left
   after the concepts, which on a wordy product it did not.
2. **The kind-ordered breadth-first walk.**
3. **The reverse walk** — and only where the first two reached no product
   page at all. A question answered entirely from `concept/pre-existing-condition`
   is a definition, not an answer about cover.

The reverse walk is guarded to a fan-in of **one**, and the guard is the whole
design. `concept/excess` is referenced by home, travel and private car, and an
early cut at a fan-in of three walked back from it to *home* — first
alphabetically — for the bare questions "deductible" and "co-payment", neither
of which is about home insurance. Two eval cases caught it. At a fan-in of one
there is exactly one product the concept belongs to, and reaching it is a fact
rather than a fact about the alphabet: the same distinction `ambiguous_focus`
draws on the lexical side.

The pipeline's product-family expansion reads the graph too. It used to build
the family by concatenating six suffixes — `/faq /cover /benefits /exclusions
/claims /conditions` — and the real corpus also files `/definitions` and
`/eligibility`.

### 3. Dense recall reaches the section — `VectorHits.by_section`

The index already holds one row per section and the query already returns
them. `by_section` keeps the `(page_id, heading)` key that `by_page` pools
away, and `select_sections` adds it to `section_relevance` in the lexical
scale, scaled from the floor up — the same shape `frontmatter_filter` uses on
the page side. A section the words already found keeps what the words gave it;
one they missed can lead.

Worth about a strong heading hit at similarity 1.0 (`DENSE_SECTION_WEIGHT =
1.0`) and deliberately no more: dense recall is evidence that a section is
*about* the question, which is what a heading match is too. It is not evidence
that the section answers it, and the procedural, benefit-token and requirement
signals that do know that still outweigh it.

Headings match by construction — the indexer and the composer both split with
`compose.split_sections`.

### 4. The RAG fallback is hybrid — `raw_chunk`

A second table, one row per `##` section of the immutable sources, split with
`api.retrieval.raw_sections` — the same function the lexical pass uses, so the
index and the search agree on what a section is and an exclusion is never
separated from the benefit it qualifies (§E.2).

A separate table rather than a `layer` column on `chunk`, because the two are
filtered differently. A wiki chunk is a compiled, approved, dated page and its
WHERE clause is the frontmatter ladder. A raw chunk has no frontmatter at all —
it is a PDF someone published — and what guards it is `okf.sources.may_support`
plus the customer's in-force version, both of which are Python. One table would
mean one query whose WHERE clause is right for half its rows.

**The safety argument is that both halves are admitted by one function.**
`_admissible` applies the marketing screen and the version filter; `_dense_hits`
adds `must_include` and the floor. A blog post, another version's wording, or a
document missing the one word the question turned on cannot enter by the dense
door having been refused at the lexical one. Four tests assert exactly that,
each at similarity 0.99.

Fusion is **reciprocal rank**, k=60. Not a weighted sum: a share-of-information
ratio and a cosine similarity are not on the same scale and never will be, and
every attempt to weight one against the other is a coefficient fitted to
whichever suite was open at the time. What the two lists genuinely agree on is
*order*, and RRF rewards that agreement — a section both rank second beats one
that either ranks first. The returned score is renormalised so the leader reads
1.0, and `RagHit.found_by` carries what the number no longer can: `lexical`,
`dense`, or `both`.

With no index configured there is no fusion at all and the lexical fallback is
byte-for-byte what it was, scores included. A deployment without pgvector
cannot tell this code was touched — asserted by a test.

## Cost

The raw index is searched **only on the turns the RAG fallback actually
fires**, which is a few per cent of them. `VectorSearch.embed` memoises its
last text, so the two searches in one turn cost one embedding call; the
searcher is built per turn, so the memo cannot answer one customer's question
with another's vector.

The backlink scans in `api.cms` and `api.store` now read the reverse index.
Each asked all 301 pages for their neighbour list, per request, to answer "who
points here" — which the graph already knew. Links only, not containment: a
page is not referenced by its own parent, and counting containment there would
have made every `/faq` and `/cover` page permanently undeletable.

## Measurement

Deterministic path, no database. The vector layers need an index and an
embedding endpoint, so what these numbers show is that the graph work is a
strict refactor plus reach — no regression anywhere:

| Suite | Before | After |
|---|---|---|
| pytest | 741 passed | 766 passed |
| golden · adversarial · merge-consistency | 9/9 · 6/6 · 3/3 | unchanged |
| staleness | 2/3 | 2/3 (the same open finding) |
| faq-customer (`okf-real`) | 344/354 | 344/354 |
| field-test (`okf-real`) | 82/109 | 82/109 |
| autoeval | 96.2%, 23 failing | 96.2%, 23 failing |

The two suites that *would* move are the ones that need the GPU box — see
below. That is the honest reading: the dense half of v2.3 is built, tested
against injected hits, and unmeasured on real embeddings.

## Open

- **Measure the dense layers.** `make index` builds both tables; the field
  test and the FAQ suite against a live index are the numbers that decide
  `VECTOR_RAW_FLOOR` and `DENSE_SECTION_WEIGHT`. Both are settings, not
  constants, for that reason.
- **A graph proximity signal in ranking.** A page one typed hop from a
  strongly-scoring seed is evidence, and `frontmatter_filter` does not use it.
  Left out deliberately: it would raise top scores and so suppress the RAG
  fallback, and that trade needs a measurement rather than a coefficient.
- Everything still open from v2.2: the 20 conflict tickets, the LLM WIKI
  drafts the cross-check left unentailed, and the GPU deployment.

---

# v2.3.1 — Routing: a refusal with somewhere to go

The measurement above produced one finding large enough to act on before
anything else. Of 379 failing turns on the golden conversation dataset, **237
are a single mode**: a question no policy document can answer, answered anyway
from whichever product page retrieval was holding.

| the customer said | after | the bot answered with |
|---|---|---|
| *"where is my claim now?"* | four turns on how to claim | the claim-notification clause |
| *"when will the refund reach me?"* | cancelling a policy | the terms of a 2024 promotion |
| *"can I pay monthly?"* | two turns on a CI plan | the S$20,000 death benefit clause |
| *"will the premium be higher?"* | a 72-year-old's cover | the leisure scuba diving list |
| *"I got an email asking me to confirm my policy details"* | — | *"Please log in to TiqConnect to update your details"* |

The last one is the argument. Every gate passed it: the sentence is a faithful
quotation of a real, approved, dated page. It is also precisely the instruction
the sender of a phishing email wants a customer to follow. **Groundedness is not
aboutness**, and no provenance check will ever catch this class — the answer's
provenance is impeccable and its subject is wrong.

## Why it happens

`classify` had no reading for these questions, so they fell to
`Intent.unknown`, which `gate_answerability` documents as *unconstrained* and
passes on purpose. Measured on the base:

```
unknown   where is my claim now?          unknown   can I pay monthly?
unknown   when will the refund reach me?  unknown   I moved house last week
unknown   how much is it?                 unknown   is this email really from you?
claim     what is my claim status         ← worse: read as a *procedure* question
```

`how much is it?` unclassified is the largest single line in the failure table
— 78 turns. Asked cold it names no product and the bot has little to say; asked
on turn three it has a product in hand and answers from its pages. Context
turns a shrug into a wrong answer, which is why the single-turn suite never
saw this.

## What changed

**A refusal is not the fix; a refusal with a destination is.** "I'm passing you
to a colleague" is a dead end — the customer cannot act on it and has no reason
to believe a rephrasing will not work. So the five intents split out of
`unknown` are refused *and routed*.

### 1. Five intents the corpus can never settle — `harness.intent`

`claim_status`, `servicing`, `payment`, `account`, `contact`, collected in
`OUT_OF_CORPUS`. The distinction they draw is not a confidence threshold. Every
other intent is a question about a **product**, and a better corpus would
answer it. These are questions about a **customer** — their claim, their money,
their account, their need for a person — and no edition of any policy document
has ever contained the answer.

Each pattern sits in front of the one that used to swallow it. `claim_status`
precedes `claim`, whose own pattern lists `claim status`. `payment` precedes
`limit`, which matched *"how much will I get back"* on the word `get`.

### 2. Routed before retrieval — `api.route`, tier 1

An out-of-corpus turn never enters retrieval: no page budget, no model call, no
eight gates to establish what the intent already settled. It is answered from
the registry and marked `handoff`.

`gate_answerability` fails the same set outright. The routing is the useful
behaviour; **the gate is the guarantee** — a draft that reaches the composer by
some other path is still refused rather than delivered.

### 3. Routed after the gates — tier 2

Everything else is a real product question the corpus may well answer, and when
it does nothing here runs. When the answerability gate refuses, the existing
`shortfall` sentence gains the page that does know: the promotions page for an
offer, the online renewal route for a general-insurance renewal, the plan's own
page for a published figure the composer could not reach.

Any *other* gate failure gets the contact page and nothing else. A groundedness
failure means the draft was faulty, which says nothing about where the answer
lives — offering the promotions page there would imply the corpus was asked and
found wanting, and it was not.

### 4. The registry — `okf.destinations`

Five addresses, supplied by the product owner on 2026-09-05, committed as a
table. Never model-generated, never lifted from a search result, for the same
reason the channel registry is not: **a URL handed to a customer is an
instruction**, and an instruction assembled at runtime out of retrieved text is
an instruction an attacker can write.

Each entry records its provenance, and a test enforces it. Four are in the
2026-08-25 crawl at status 200, so the corpus is evidence for them. The two
`/LoginPortal/#/` routes are not and cannot be — they are client-side fragments
behind a login, which no crawler resolves — so they claim `owner`, and the test
asserts that anything claiming `owner` is one of them.

**The product's own page is deliberately not in the registry.** It is already
in the corpus, on each product page's `channel/direct` binding, and the
channel-coherence gate already treats it as this route's own address. Copying
37 URLs into a Python file would create a second place for them to rot.

`renews_online` resolves the renewal destination rather than tabling it: a
general-insurance policy renews through the online renewal route and a life,
protection or savings policy does not, so one page for every renewal question
would be wrong for a third of the catalogue.

### 5. Fraud goes to a person, and only to a person

A message the customer is checking the provenance of never reaches retrieval
and never receives the portal. Telling someone who may have just been phished
to go and log in somewhere is the answer this whole change exists to stop
giving.

## Cost, and what it does not do

Out-of-corpus turns get **cheaper** — they were spending a retrieval, a model
call and eight gates to produce a wrong answer.

Two deliberate omissions:

- **A bare *"how much will I get back?"* is still read as a limit.** After a
  cancelled *policy* it is a premium refund; after a cancelled *trip* it is the
  trip-cancellation benefit, which is a figure in the benefit table. An early
  cut read the second as the first and sent three real figure questions to the
  customer portal. Only the forms that name a refund, or ask when one lands,
  route — the safe half of the ambiguity, and the eval pays for the rest.
- **Family B is untouched.** 115 turns are the opposite failure: the bot
  refuses a question the corpus *does* answer — *"I want to cancel Commercial
  Vehicle Insurance"* when `conditions.md` carries the clause. That is a
  query-formulation problem and routing must not paper over it; a destination
  offered instead of an answer the corpus holds is a regression wearing a
  helpful face.

## Measured

Same 1,711-case dataset, same pinned evaluation date, deterministic composer:

| | v2.3 | v2.3.1 |
|---|---|---|
| overall | 1209/1711 · 70.7% | **1333/1711 · 77.9%** |
| whole conversations | 132/355 · 37.2% | **198/355 · 55.8%** |
| turns | 994/1373 · 72.4% | **1180/1373 · 85.9%** |
| **pivot** | 18/165 · **10.9%** | **124/165 · 75.2%** |
| drill | 219/304 · 72.0% | **291/304 · 95.7%** |
| handoff contract | 43/179 · 24.0% | **96/179 · 53.6%** |
| `pay` journey | 3/23 · 13.0% | **18/23 · 78.3%** |
| `service` journey | 7/26 · 26.9% | **18/26 · 69.2%** |
| product_fact | 974/1092 · 89.2% | 974/1092 · 89.2% |
| owed a handoff, gave none | 145 | **87** |

**124 cases gained, 0 lost.** Gains by turn intent: premium +66, claim_status
+40, payment_method +37, refund_status +35.

`product_fact` holding at exactly base's figure is the number that mattered
most. Routing that improved the transactional journeys by eating the questions
the corpus is for would have been a worse bot with a better score.

### Three defects the measurement corrected

All three were mine, and none was visible without running the suite.

**A bare *"how much?"* is a limit question.** On turn three of *"does Corporate
Travel pay for an 8-hour delay?"* it asks for the benefit. The first
bare-price pattern read it as a price question and turned three answered limit
questions into refusals. The object is the whole distinction: *"how much is
it"* is about the plan, *"how much"* alone is about whatever was last
discussed.

**Paying *for a plan* is not paying *a premium*.** *"How do I pay for Tiq
Travel Insurance?"* is a question about buying, the product page answers it,
and a bare `how do I pay` clause routed it to the portal. What belongs in
`payment` is a schedule or a method.

Fixing the second exposed the reverse: reading the dataset's own 224 handoff
cases end to end found fourteen phrasings the intent missed — *"Can I pay by
credit card?"* (the method list wanted the method word first, and "credit"
came before "card"), *"My card was declined"*, *"Has my claim payment been
processed?"*, and *"Someone called me claiming to be your agent and asked for
payment"*, a fraud report the corpus would have answered.

**A heading is never out of corpus.** An earlier cut lost three cancel/renew
turns on Cancer Insurance, and the first explanation — order-dependence in the
batch — was wrong: every "isolation" replay behind it had silently run against
the seed bundle, which has no Cancer Insurance product, so base and branch
agreed because neither could find the page. Against the real bundle, base
answered and the branch did not. `faq_pick` counts FAQ headings whose
`classify()` matches the question's intent to decide how strict to be; the new
`payment` pattern read *"When will GIRO deductions be made for the renewal
premium?"* as out-of-corpus, the renewal count fell from two to one, a
low-overlap FAQ entry led on the lenient threshold, and its first sentence —
*"The premium for your policy is guaranteed"* — is an entitlement assertion to
an anonymous session, which that gate rightly blocks. The defect was applying
a *question* classifier to *corpus text*. `classify_topic` is `classify` with
the out-of-corpus patterns skipped; on all 467 FAQ headings in the real corpus
it agrees exactly with what `classify` said before the split existed, and
`faq_pick` now reads headings through it. The same scan found a bare `fraud`
sending *"does it cover credit card fraud?"* to the contact page; fraud is now
something reported or suspected, never something covered.

The check that made all of this safe is one pass over both sets at once: every
`handoff` case and every `product_fact` case, asserting which route. Zero
product-fact questions route today, and the suite is deterministic — forwards
and reversed produce byte-identical answers on all 1,711 cases. Two tests hold
both directions of the routing patterns, and five more pin the moved headings,
so the next widening of these patterns has to face the same questions.
