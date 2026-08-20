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

Two more pieces close the loop from the live websites to the wiki:

| Stage | Here |
|---|---|
| **Crawl** — allowlisted, robots-obeying, dated snapshots | `apps/crawler` — `make crawl` |
| **Compile** — snapshots → one canonical page per product | `apps/compiler/compiler/wiki.py` — `make wiki` |

> **Fixture data.** The seed bundle is for development: tier names are
> `tier-1..3` placeholders and every number is invented. Real values come from
> `raw/wordings` and `raw/product-summaries` through the compile loop and must
> be reviewed before any page is marked `approved`.

> **The real sites are not reachable from this environment.** `www.etiqa.com.sg`
> and `www.tiq.com.sg` are refused by the egress policy (the proxy returns 403
> to CONNECT), so the corpus in `okf-web/` is compiled from a **synthetic
> fixture site** on IANA-reserved `.example` hosts. Every page of it carries a
> banner saying so and every figure in it is invented. Nothing in this
> repository asserts a real Etiqa or Tiq product fact. When egress is granted,
> `make crawl` points the same crawler at the real hosts and the compile step
> is unchanged.

## Run it

```bash
uv sync
make console        # → http://localhost:8080
```

Three surfaces come out of the same process:

| URL | What it is for |
|---|---|
| `/` | **Debug console** — ask a question, see why that answer came out |
| `/studio` | **Content studio** — review the corpus, scan the websites, publish |
| `/docs` | **API** — the OpenAPI surface partners integrate against |

The **debug console** runs the full serve loop and shows you why the answer came
out the way it did:

- the **answer**, with every number highlighted and hover-bound to its table row
- **claims → sources**, each with its page id and raw locator
- **seven gate verdicts**, and the blocked draft when one refuses delivery
- the **frontmatter filter**: pages admitted *and pages rejected with the reason*
- **graph traversal**: which pages were reached by following links, and at which hop
- **RAG fallback**: whether it fired and why
- **budgets**, stage latencies, and the raw trace JSON
- a **page inspector** (click any page id) and an in-browser **eval runner**

## Gathering the corpus

```bash
make crawl          # the real hosts, once egress allows it
make knowledge      # crawl the fixture site → compile → lint (what CI runs)
make autoeval-web   # evaluate against the compiled corpus
```

The crawler is deliberately unclever and very polite: host-equality allowlist
(not substring — `www.etiqa.com.sg.example.test` is a different site), robots.txt
with longest-match Allow/Disallow and `Crawl-delay`, one token bucket per host,
sitemap → sitemap-index → WordPress REST → bounded link crawl for discovery,
and content-hashed snapshots written to `raw/web/<host>/<date>/`. PDFs — the
wordings and product summaries, which are the *highest* authority — are
recorded as an inventory rather than chunked as web copy.

The compile step is where the design earns its keep:

| Crawled | Compiled |
|---|---|
| the same plan on two brand sites | **one** `product/<line>/<slug>` page with two channel bindings |
| a benefit table in HTML | rows in `raw/benefit-tables/<slug>.csv`, prose keeps `{{table:…}}` |
| a "What is not covered" section | its own exclusions page, linked — traversable, not hoped for |
| a claims or servicing page | a `journey/` page |
| two sites disagreeing on a figure | the higher-authority value, plus a **website defect ticket** |
| any page at all | `status: draft` — `--sign-off` is what a human review records |

Nothing compiled is retrievable until someone signs off: the frontmatter filter
admits `approved` pages only, so an unreviewed compile answers nothing rather
than answering unreviewed.

## The content studio (`/studio`)

Where a content owner works. It is a writing surface over the same bundle the
serve loop reads, so a change is live for the next question — and the rules
that make the corpus trustworthy are enforced on the way in rather than
reported afterwards.

**Reviewing.** Filter by type, status, tag, line of business, or "needs
attention"; every page shows whether it is actually *answering customers* —
approved, in its effective window, not overdue — which is a different question
from whether it exists. Open one and you get its frontmatter, its graph edges
in both directions, its lint verdicts, and the benefit-table rows its figures
are fetched from.

**Scanning.** One button crawls `etiqa.com.sg` and `tiq.com.sg` into a
**staging** bundle — never over the live corpus — compiles it, and diffs the
result against what is published. The output is a review queue, ordered by what
a customer would notice:

| Finding | Why it is ranked there |
|---|---|
| `figure-drift` | the site now publishes a different number; the assistant is confidently quoting the old one, *with a citation* |
| `website-defect` | the two sites disagree with each other — a defect in a website, not in the wiki |
| `new-page` | a product on the site that the wiki does not describe at all |
| `content-drift` | the wording moved |
| `review-overdue` | already demoted out of wiki-first retrieval |

Adopting a suggestion writes a **draft**. A scan proposes; a person disposes.

**Authoring.** Hand-written content is not exempt from provenance: your
supporting material is saved to `raw/custom/` and becomes the page's authority,
so every `[src:…]` resolves to a real file and reference-integrity treats the
page like any other. A number typed into prose, an unreferenced claim or a
broken link **refuses the save** and comes back as a violation you can act on —
the linter is a thing you work with, not a thing you hit at publish time.

**Approval is a signature.** Promoting a page records who signed it off and
when it must be looked at again; only then does it become retrievable.

**Evaluation, in the same place.** The generated suite runs in-process against
the current corpus, because publishing a page and then going to find a terminal
is how the eval step gets skipped.

**Integrations.** What the service calls and what calls it, each with what
happens when it is absent — plus a live probe. The crawl-egress probe is the one
that matters operationally: it reports the proxy's answer verbatim, so a blocked
host reads as a blocked host rather than as a broken crawler.

## The four loops (§G)

| Loop | Cadence | Command |
|---|---|---|
| 1 · Serve | per turn | `make console` → `/`, or `POST /v1/answer` |
| 2 · Compile | nightly / on publish | `make crawl`, `make wiki`, `make conflicts`, `make lint-bundle` |
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
| a channel page | "how do I contact X?" — with the *other* brand's hotline as a forbidden string |
| an entity page | "who underwrites this?" |
| an FAQ the website itself publishes | that question, asked back |
| a product line the corpus does **not** carry | must hand off, never answer from the nearest neighbour |

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

**Refusing to answer is a feature.** "What does your crop insurance cover?"
used to return the home-insurance page: every word in it except *crop* is
corpus-wide vocabulary, so a bag-of-words score looked respectable. Retrieval
now weights terms by corpus IDF, and a word the corpus has never seen sitting
in front of a product head word ("crop **insurance**") is treated as naming a
line we do not carry — the wiki filter, the RAG fallback and the composer all
decline, and the turn hands off. Which product a question is about is decided
by contiguous title and alias matches rather than term overlap, so "the
personal accident limit on **Maid Insurance**" reaches the maid tables and not
the Personal Accident product that owns both words.

## Layout

```
okf/                    the seed bundle — hand-written, small; the curated suites run here
  okf.yaml              manifest: taxonomy, authority order, link rules
  raw/                  IMMUTABLE sources: wordings, product summaries,
                        benefit tables (CSV), crawl snapshots, regulatory
  wiki/                 COMPILED pages: product · concept · journey ·
                        channel · entity · promotion
  conflicts/            unresolved source disagreements → human queue
  log.md                append-only operation log
okf-web/                build output: crawled + compiled. `make knowledge` regenerates it
fixtures/               the synthetic two-brand site the crawler is proved against
packages/okf/           page model, frontmatter schema, tables, graph, linter, corpus IDF
packages/harness/       contracts, seven gates, budgets, traces
apps/api/               serve loop, debug console, content studio, content API
apps/crawler/           allowlist + robots policy, extraction, dated snapshots
apps/compiler/          snapshot → wiki compile, fact extraction, conflicts, impact
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
conflict detection with impact analysis, the four eval suites wired to a CI
gate, the allowlisted crawler, the compile step that turns crawl snapshots into
canonical pages, benefit-table CSVs and website defect tickets, and the content
studio — review, scan-and-verify, authoring, status workflow, tagging,
in-process evaluation and the integration registry.

Not built:

- **Composing answers from retrieved clauses** (their K4). A historic-version
  question correctly triggers RAG, retrieves that version's wording, and is
  then **blocked by `version-coherence` and handed off** rather than answered
  from the current wiki page. That is the safe outcome, and the console shows
  the retrieved clauses — but turning them into a cited answer is the next
  piece of work.
- **LLM-backed extraction and composition** under guided decoding. The
  contracts and the fact/prose separation are in place; the deterministic
  composer is the fallback and stays the offline path.
- **Langfuse export** — traces have the right shape, nothing ships them yet.
- **Authentication on the content API.** Every write is linted, but nothing
  yet asks *who* is writing: `actor` is a field the caller supplies, not an
  identity the service verifies. Sign-offs are only as trustworthy as that.
- **Durable scan jobs.** The registry is in-process, so a restart loses a
  running scan's suggestions. The shape — submit, poll, act — is what a queue
  would keep.
- **pgvector dense retrieval.** `§J.1` recommends grep + frontmatter filter
  until measured recall degrades; that is what this implements, and the
  rejected-candidate log is how you would detect the degradation. The
  auto-eval reports recall@k and MRR, so "measured" is now literal.
- **LLM-widened paraphrasing** in the generator. Question phrasings are
  template-derived; a configured endpoint could widen them, and the
  deterministic templates stay the floor.
