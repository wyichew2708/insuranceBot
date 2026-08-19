# Etiqa SG Knowledge Layer — OKF wiki + RAG + harness

Implementation of *Implementation Design — Knowledge & Harness Layer*: a
**wiki-first** insurance assistant over a unified Etiqa Singapore corpus, with
RAG as the fallback for the long tail and a verification harness between
generation and delivery.

The four layers, wired into loops rather than chosen between:

| Layer | Here |
|---|---|
| **LLM Wiki** — knowledge compiled once, not re-discovered per query | `okf/wiki/` — one canonical page per product |
| **OKF** — the portable, lintable file format | `packages/okf` — frontmatter schema, graph, linter |
| **RAG** — the long tail and the raw source of truth | `apps/api/api/retrieval.py` — fallback over `okf/raw/` |
| **Harness** — what makes it safe to answer at 2am | `packages/harness` — contracts, seven gates, budgets, traces |

> **Fixture data.** The seed bundle is for development: tier names are
> `tier-1..3` placeholders and every number is invented. Real values come from
> `raw/wordings` and `raw/product-summaries` through the compile loop and must
> be reviewed before any page is marked `approved`.

## Run it

```bash
uv sync
make console        # → http://localhost:8080
```

The **debug console** is the front door. It runs the full serve loop and shows
you why the answer came out the way it did:

- the **answer**, with every number highlighted and hover-bound to its table row
- **claims → sources**, each with its page id and raw locator
- **seven gate verdicts**, and the blocked draft when one refuses delivery
- the **frontmatter filter**: pages admitted *and pages rejected with the reason*
- **graph traversal**: which pages were reached by following links, and at which hop
- **RAG fallback**: whether it fired and why
- **budgets**, stage latencies, and the raw trace JSON
- a **page inspector** (click any page id) and an in-browser **eval runner**

## The four loops (§G)

| Loop | Cadence | Command |
|---|---|---|
| 1 · Serve | per turn | `make console`, or `POST /v1/answer` |
| 2 · Compile | nightly / on publish | `make conflicts`, `make lint-bundle` |
| 3 · Evaluate | every publish | `make evals` (curated) · `make autoeval` (generated) |
| 4 · Evolve | weekly | trace review; each fix ships a new eval case |

## Auto-evaluation

```bash
make autoeval      # generate → run → score → report
```

Rather than hand-writing an eval suite, `apps/evalgen` **derives one from the
corpus**. Every case comes from something already in the bundle:

| Source in the corpus | Generated case |
|---|---|
| a benefit-table row | "What is the *X* limit?" pinned to that row id and tier |
| an authored alias | one question per alias — none goes untested |
| an exclusion section | "Are *X* covered?" expecting the exclusions page |
| a concept page | "What does *X* mean?" |
| a journey page | "How do I go about *X*?" |
| a multi-channel product | a merge pair — same facts, both brand framings |
| an effective window | live promotions quotable, expired ones not |
| a superseded version | expected to be *refused*, not answered |
| a detected source conflict | the wrong website figure, offered as bait |

Two things follow. The suite **grows with the corpus** — publish fifty product
pages and their questions appear without anyone writing YAML. And coverage
becomes **measurable**: any page no question reaches, or table row no answer
exercises, is named in the report.

Metrics span four families — correctness (citation precision/recall/F1, figure
exact match, numeric-binding integrity), retrieval (recall@1/3/5, MRR, graph
contribution), safety (entitlement leaks, conflict resistance, advice
boundary), and performance (latency percentiles overall and per stage,
throughput, budget use). Every failure is routed to one of the five Loop 4
buckets, so the report ends with owners rather than numbers.

Output lands in `.eval-reports/`: `auto-eval.json` for trending,
`auto-eval.md` for the repo, `auto-eval.html` to read.

## What the design buys you, concretely

**Brand is a channel attribute, not a product identity** (§B.1). One
`product/general/travel` page carries both channel bindings. The same question
returns identical facts on either surface and differs only in the rendered deep
link — enforced by the `merge-consistency` suite, not by convention.

**Numbers are never generated** (§C.3). Prose carries
`{{table:medical_expenses.limit}}`; the harness does a deterministic row fetch
against `(product, version, tier)` and keeps the `row_id` on the figure. The
`numeric-binding` gate blocks any number in the answer that no row produced —
there is no retry with "please be careful".

**Graph traversal replaces multi-hop RAG** (§E.1). "Is my pre-existing condition
covered?" loads the product page, follows `links.exclusions`, then
`concept/pre-existing-condition`. Three deterministic reads, complete exclusion
set guaranteed.

**The assistant audits the websites** (§D.2). `make conflicts` compares every
raw source against the benefit tables under the declared authority order. The
seed bundle ships with a planted disagreement — the Tiq page says the delay
benefit starts after 4 hours; the tables say 6 — and it is filed as a *website
defect ticket*, not a wiki problem.

**Honest degradation** (§F.1). An anonymous session has no plan tier, so the
tier-specific limit is left `[unavailable]` and named in `unresolved` rather
than guessed.

## Layout

```
okf/                    the bundle — knowledge is code
  okf.yaml              manifest: taxonomy, authority order, link rules
  raw/                  IMMUTABLE sources: wordings, product summaries,
                        benefit tables (CSV), crawl snapshots, regulatory
  wiki/                 COMPILED pages: product · concept · journey ·
                        channel · entity · promotion
  conflicts/            unresolved source disagreements → human queue
  log.md                append-only operation log
packages/okf/           page model, frontmatter schema, tables, graph, linter
packages/harness/       contracts, seven gates, budgets, traces
apps/api/               serve loop + debug console
apps/compiler/          fact extraction, conflict detection, impact analysis
evals/suites/           golden · merge-consistency · adversarial · staleness
```

## The seven gates (§F.2)

| Gate | Blocks when |
|---|---|
| reference-integrity | a cited source is missing, unapproved or out of window |
| numeric-binding | a number in the answer traces to no table row or SOR field |
| version-coherence | cited pages mix versions, or contradict the in-force policy |
| channel-coherence | the render disagrees with the session, or leaks the other brand's contact |
| exclusion-completeness | coverage is asserted without the exclusion page having been read |
| advice-boundary | advice is sought or the product is regulated, and no adviser handoff |
| groundedness | a claim is not entailed by the pages actually loaded |

## Configuration

Everything external is optional — with nothing configured the loop runs
deterministically, which is what keeps the console and the eval suites usable
offline.

```
BUNDLE_PATH=okf
VLLM_BASE_URL=          # unset → deterministic composer
VLLM_MODEL=
MAX_PAGES=8             MAX_TOOL_CALLS=6
MAX_WALL_CLOCK_S=10     MAX_TOKENS=20000
WIKI_READ_LIMIT=5       CANDIDATE_FLOOR=0.08     CONFIDENCE_FLOOR=0.45
```

## Status

Built: the OKF bundle contract and linter, wiki-first retrieval with graph
traversal, deterministic numeric binding, RAG fallback over `raw/`, the SOR
entitlement stub, all seven gates, budgets, full tracing, the debug console,
conflict detection with impact analysis, and the four eval suites wired to a
CI gate.

Not built:

- **Composing answers from retrieved clauses** (their K4). A historic-version
  question correctly triggers RAG, retrieves that version's wording, and is
  then **blocked by `version-coherence` and handed off** rather than answered
  from the current wiki page. That is the safe outcome, and the console shows
  the retrieved clauses — but turning them into a cited answer is the next
  piece of work.
- **The crawler** that populates `raw/web/` (snapshots here are fixtures).
- **LLM-backed extraction and composition** under guided decoding. The
  contracts and the fact/prose separation are in place; the deterministic
  composer is the fallback and stays the offline path.
- **Langfuse export** — traces have the right shape, nothing ships them yet.
- **pgvector dense retrieval.** `§J.1` recommends grep + frontmatter filter
  until measured recall degrades; that is what this implements, and the
  rejected-candidate log is how you would detect the degradation. The
  auto-eval reports recall@k and MRR, so "measured" is now literal.
- **LLM-widened paraphrasing** in the generator. Question phrasings are
  template-derived; a configured endpoint could widen them, and the
  deterministic templates stay the floor.
