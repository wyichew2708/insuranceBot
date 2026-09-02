# v2.1 — what changed from v2, and why

Branch `claude/genai-insurance-chatbot-v2.1`, eleven commits on top of v2 at
`7bc8371`. Thirty-seven files, +2,139 / −62 lines. This document is the
reference for the differences; DESIGN-answering.md describes the v2 answering
design it builds on, and DEPLOYMENT.md §8a covers the GPU host.

## In one paragraph

v2 made the bot faithful to the corpus: every claim traces to an approved
page, every figure binds to a row or a quoted span, and nine gates verify
that after the fact. v2.1 starts from a measured finding about what that left
uncovered — **a true, cited, grounded statement about the wrong thing passes
every gate** — and adds the second principle the system was missing: *an
answer must address the question that was asked, or say that it cannot.*
Around that it adds a recall layer (pgvector, fused into the lexical rank and
bound to the same filters), a plain-language content tier the model writes
offline under quotation binding, and the infrastructure to run the whole
stack on either the Mac or a Linux GPU host by changing a `.env`.

## The numbers that drove it

Measured on v2, 1,000-case random sample of the generated suite, live on Qwen:

| | v2 as reported | v2 re-counted (v2.1 metric) |
|---|---|---|
| accuracy | 84.5% | 84.5% |
| unsafe (a wrong answer delivered) | 79 | **114** |
| wrong product delivered | not counted | **11** |
| safe misses (declined) | 76 | 41 |

The metric had no branch for "delivered, but cited the wrong page", so those
35 cases were scored as the bot *declining* — the opposite of what happened.
Eleven of them were critical-illness riders read out as each other at 0.99
confidence. That correction is the first v2.1 commit, and every later change
is judged against the 114, not the 79.

Two further findings from reading all 155 failures:

- **Confidence is not a signal.** Unsafe answers averaged 0.955; correct ones
  0.923. No threshold separates them. Any design of the form "refuse below X"
  was dead on arrival.
- **The gap is refusal, not fabrication.** 61 of the 79 answered when they
  should have declined. Zero leaked a forbidden string; zero let an unbound
  figure through; only 1 of 79 tripped any gate. The canonical case: *"What
  is the premium for life insurance?"* answered with a true clause *about*
  premiums.

Field test (104 customer-phrased turns, v2): 40.4% → 75.0% across the v2
fixes. The v2.1 figures for both suites are being measured live as this is
written and will be appended to EVALUATION.md.

## What changed, by layer

### 1. Measurement

| v2 | v2.1 |
|---|---|
| `did not cite` fell through `severity` to "miss" | delivered + wrong page = **unsafe** |
| — | `wrong_product` reported as its own number |
| batch runner refuses a run the model did not serve | …and reports how many turns the entailment judge actually judged vs fell back |

### 2. Answering — the defences

Each closes one class from the failure analysis. All are deterministic except
the entailment judge, which uses the configured model and falls back to the
v2 test when there is none.

| class | v2 | v2.1 |
|---|---|---|
| **price** | `Intent.price` required a premium figure *or* the word "premium" — clauses are OR'd, so a clause containing the word passed | figure only; otherwise the honest shortfall ("premiums are not published in the documents I answer from") |
| **entity** | no intent; "who underwrites this" fell to `unknown` and the gate skipped it | `Intent.entity`; deterministic answer from the entity page (all 670 product pages share one underwriter); backstop requirement `needs_page_type=("entity",)`; page **titles** now count as gate evidence |
| **recommendation** | `ADVICE_SEEKING_RE` missed "recommend I take", "is this enough cover" | widened to the request forms only — "recommended documents for a claim" still passes |
| **model may decline** | rewrite prompt forbade inventing a number, said nothing about inventing a relevance | rule 6: if the facts do not answer the question asked, say so and put it in `unresolved` |
| **identity** | model's catalogue pick pinned as focus; a rider read out as its sibling | a product named **in full** in the customer's own words overrules the pick; two named → clarify. Read *before* abbreviation expansion, which rewrote "Tiq PA Insurance" into another product's title |
| **figure ↔ benefit** | any bound figure satisfied a limit question; S$150,000 delivered where section 6 said S$20,000 | where the question names a benefit (vocabulary or "section N"), a figure must bind to *that* row |
| **sense** | groundedness = token overlap ≥ 0.6; "covers you on the death of" and "cover ends on the death of" share every token | the model judges entailment per load-bearing claim: `contradicts` fails; `neutral` fails only on an amount or rate; everything the judge does not vouch for falls to overlap. Silent judge → lexical, and the trace says so |
| **contested figures** | 20 compiler conflict tickets, nothing at answer time knew | bundle reads them at load; a figure on a contested row is delivered with the dispute named and which value takes precedence |
| **omission** | an answer that never mentioned the benefit asked about was grounded and passed | where a named benefit appears on no *loaded* page: "The pages I answer from do not address X for Y." |

The judge went through three calibration rounds against real turns, each
fixing a cause rather than loosening the rule: it was shown the first 1,500
characters of a 15,000-character section; a definition's "twelve (12) months"
was treated like a limit; and eight claims were judged while the rest were
reported as entailed and checked by nothing. All three are tested.

### 3. Retrieval — the recall layer

| v2 | v2.1 |
|---|---|
| lexical only; a share-of-information ratio that ties 87–213 pages on ordinary questions | **hybrid**: pgvector similarity over compiled wiki sections, pooled to page, fused into `scored` as a bonus (like alias and focus bonuses) *before* the frontmatter filter runs |
| `rag_search` over `raw/` — whose hits never reached an answer (write-only) | the vector index is the **wiki**, not raw/: wiki sections carry status, effective window, jurisdiction, channel; the SQL `WHERE` repeats the filter ladder so a stale chunk is never the nearest neighbour |
| — | `must_include` post-filter on vector candidates (cosine will return marine hull for "crop insurance"); `vector_floor` below which a hit earns nothing |
| — | optional cross-encoder reranker (`bge-reranker-v2-m3`) over the fused top-20 |
| — | `retrieval_mode` (`lexical` / `hybrid`) and `vector_degraded` on every trace |

Off by default. With `PGVECTOR_DSN` empty the API runs exactly as v2, no stage
opened, no connection tried.

### 4. Content — a new tier

```
raw/wordings > raw/product-summaries > LLM WIKI > raw/benefit-tables > raw/web > raw/blog
```

`make llm-wiki` has the configured model write, per approved product, one
plain-language page — what it covers, what it does not, how to claim, five
questions people ask — from the product's *compiled* pages, never from raw.
Every sentence must cite the one compiled section it was written from, by id,
and only sections it was shown; every figure must appear verbatim in that
section (quotation binding at write time). Failing sentences are dropped and
counted; a page with fewer than four survivors is not written. Everything
lands `status: draft`, `compiled_by: llm`, retrievable by nothing until a
human reviews it. Without a model the tier does not exist.

### 5. Infrastructure

| | v2 | v2.1 |
|---|---|---|
| compose | one service | `api` unchanged; **`gpu` profile** adds `postgres` (pgvector/pgvector:pg16, named volume), `embed` (TEI + bge-m3), `rerank` (TEI + bge-reranker-v2-m3), `vllm` |
| settings | — | `pgvector`, `pgvector_dsn`, `embed_base_url`, `embed_model`, `rerank_base_url`, `pgvector_fail_closed`, `vector_floor` |
| packaging | `anthropic` extra | + `pgvector` extra (`psycopg[binary]`, `pgvector`); Containerfile syncs both; compiler now depends on `api` for the provider |
| index | — | `infra/pgvector/schema.sql`; `scripts/index_pgvector.py`; `make index` — offline, content-hash keyed, never inside `Bundle.load` |
| integrations | — | `pgvector` entry with a probe that reports the database's own error verbatim |
| Makefile | `docker-logs` hardcoded `api` | `SERVICE ?= api`; `index`; `llm-wiki` |
| docs | "no vector store" | DEPLOYMENT.md §8a; README amended rather than contradicted |

### 6. Compiler

One product, `etiqa-homeowners-enhanced`, was dropped by the Phase 4
recompile: its wording has no "what is covered" section and the compiler
required one to emit a product at all. "Not insured" now classifies as an
exclusions heading, "basis of settlement" as conditions, and a wording with
exclusions and conditions but no cover section is opened from its first
compiled section rather than dropped. Recompile pending the current
verification run, so the measurement stays on one corpus.

## Configuration reference (new in v2.1)

| variable | default | meaning |
|---|---|---|
| `PGVECTOR` | `auto` | `auto` = on if a DSN is set, else lexical; `on` = fail the turn if unreachable; `off` = never connect |
| `PGVECTOR_DSN` | empty | `postgresql://okf:okf@gpu-host:5432/okf` |
| `EMBED_BASE_URL` | empty | OpenAI-compatible `/v1/embeddings` (TEI); the API embeds only the question |
| `EMBED_MODEL` | `BAAI/bge-m3` | 1024-d, multilingual |
| `RERANK_BASE_URL` | empty | optional TEI reranker |
| `PGVECTOR_FAIL_CLOSED` | `false` | open by default, like the guardrail screen |

Two machines, one configuration:

```
# Mac (MLX on the host, no profile)     # GPU host
VLLM_BASE_URL=http://localhost:8090     VLLM_BASE_URL=http://gpu-host:8000
PGVECTOR_DSN=                           PGVECTOR_DSN=postgresql://okf:okf@gpu-host:5432/okf
EMBED_BASE_URL=                         EMBED_BASE_URL=http://gpu-host:8080/v1
```

## Sequencing — why in this order

Vector retrieval fixes recall. It does nothing for the unsafe classes and
makes the largest one worse: a retriever that finds plausible content more
readily finds plausible content for the wrong question more readily. So the
defences landed first (commits 1–7), retrieval behind them (8–9), the content
tier last (10–11). Every step is measured on both suites; the number that
decides is unsafe as re-counted, and it may fall only by becoming correct or a
safe miss — never by editing a suite.

## What is not done

- **Verification numbers for v2.1** — running as this is written; EVALUATION.md
  will carry them. The v2 figures above are final.
- **The recompile** for the homeowners fix — held until the run finishes.
- **The LLM WIKI tier has not been generated** on the real bundle; the
  generator is built and tested against the contract, and the first run needs
  a human to read a sample before any page leaves `draft`.
- **Two logged defects**: the composer emits bulleted list items as standalone
  claims (the judge rightly will not vouch for them); the browse path lists
  one of the two PA products for "do you have PA cover".
- **Nothing is pushed.** The branch is local.

## Commits

```
002b7c9  Count a confident answer from the wrong page as unsafe, because it is
2bc2876  Answer the question that was asked, or say that you cannot
2d9d8d2  The product the customer named is the product answered
ba90083  Read the product's name before anything rewrites it; bind the figure to the benefit asked
a2e0a00  Judge whether the evidence means what the answer says, not whether it shares its words
426fd82  Calibrate the judge on what it was actually shown
5c0eec1  Every claim the judge does not vouch for is checked by something
b392184  Dense retrieval over pgvector, bound to the filters it cannot bypass
577cae1  A cross-encoder over the fused top-20, and the docs stop saying "no vector store"
f675611  Say when a figure is disputed; write the plain-language tier, draft and gated
4177480  Say what was not found — and do not lose a product for lacking a cover section
```
