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
