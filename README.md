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
> repository asserts a real Etiqa product fact. When egress is granted,
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
| the same plan on both websites | **one** `product/<line>/<slug>` page, one channel binding, two front doors |
| a benefit table in HTML | rows in `raw/benefit-tables/<slug>.csv`, prose keeps `{{table:…}}` |
| a "What is not covered" section | its own exclusions page, linked — traversable, not hoped for |
| a claims or servicing page | a `journey/` page |
| two front doors disagreeing on a figure | the higher-authority value, plus a **website defect ticket** |
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
| a multi-route product | a merge pair — same facts on every route to market |
| an effective window | live promotions quotable, expired ones not |
| a superseded version | expected to be *refused*, not answered |
| a detected source conflict | the wrong website figure, offered as bait |
| a channel page | "how do I contact X?" — with another *route's* contact as a forbidden string |
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

**A channel is a route to market, not a product identity** (§B.1). Etiqa and
Tiq are not two brands to choose between: they are two front doors of the one
`channel/direct`, alongside bancassurance, agency, broker and IFA. A customer
starts from the *product*, never from a brand, and one
`product/general/travel` page serves all of them. The same question returns
identical facts on every route and differs only in the rendered deep link —
enforced by the `merge-consistency` suite, not by convention.

| Channel | How the customer buys | Front doors |
|---|---|---|
| `channel/direct` | online, self-serve | `www.etiqa.com.sg` **and** `www.tiq.com.sg` |
| `channel/bancassurance` | bank relationship manager | partner bank |
| `channel/agency` | tied agent | find-an-agent |
| `channel/broker` | broker acting for the customer | broker |
| `channel/ifa` | independent financial adviser | FA firm |

Both direct front doors stay reachable and either may be cited in an answer —
citing one while the render names the other is *not* a leak. What the
`channel-coherence` gate still blocks is offering a **different route** (handing
a direct customer the agency link), because that changes how they buy.

**Numbers are never generated** (§C.3). Prose carries
`{{table:medical_expenses.limit}}`; the harness does a deterministic row fetch
against `(product, version, tier)` and keeps the `row_id` on the figure. The
`numeric-binding` gate blocks any number in the answer that no row produced —
there is no retry with "please be careful".

**The contract outranks the marketing page** (§D.1). The crawler records PDFs
without parsing them, which would leave the two highest-authority tiers empty
and let a marketing headline stand unchallenged. `crawl documents` fills them:

```bash
uv run python -m crawler.cli run --allowlist www.etiqa.com.sg www.tiq.com.sg --out okf-real/raw
uv run python -m crawler.cli documents --manifest okf-real/raw/web/crawl-manifest.json --out okf-real/raw
```

Documents route by filename into `raw/wordings`, `raw/product-summaries`, or
`raw/brochures` (never authoritative), and three backends satisfy one
contract. Measured on a real 46-page policy wording:

| `--backend` | Tables | Time | Cost |
|---|---|---|---|
| `markitdown` *(default)* | **yes** — recovers every benefit row | **3.9s** | pdfplumber, megabytes |
| `docling` | yes, with cleaner column boundaries | 29s | `uv sync --extra docling`, ~2GB |
| `builtin` | **none** — flattened to prose | 2.4s | pypdf |

The difference between the first two and the last is not cosmetic. The
builtin backend returns 155k characters and **zero tables** — the Table of
Benefits arrives as a paragraph, so every limit in it is lost. Both
table-capable backends recover the row that matters, identically:

    Adult aged below 70 years old  $200,000  $1,000,000  $2,500,000

`auto` prefers markitdown: it gets the same rows as docling at a seventh of
the cost, which across a few hundred documents is the difference between
fifteen minutes and two hours. Reach for docling where explicit column
boundaries matter — markitdown runs a row's cells together where the PDF has
no ruling lines. A run that produced no tables says so rather than reporting
a clean ingest.

**OCR is off by default** and that is a 9× difference: insurer PDFs carry a
text layer, so OCR re-reads pixels to recover text that was already there —
272s versus 29s for identical output. `--ocr` is there for genuine scans,
which the builtin backend flags as "no extractable text".

**The model is swappable because it never establishes a fact** (§H.1). Retrieval
picks the pages, the transclusion pass resolves every figure against a
benefit-table row, and the composer lifts each `[src:...]` marker into a typed
claim. Only then is a model asked for anything, and what it is asked for is
*prose*. Three providers satisfy one contract:

| `LLM_PROVIDER` | Engine | Needs |
|---|---|---|
| `deterministic` | the composer's own wording | nothing — no network, no key |
| `anthropic` | Claude Sonnet 5 via the official SDK, under structured outputs | `ANTHROPIC_API_KEY` (or `ant auth login`) |
| `vllm` | a model you host, over vLLM's OpenAI-compatible route | `VLLM_BASE_URL`, `VLLM_MODEL` |
| `auto` *(default)* | local endpoint if configured, else Anthropic, else deterministic | — |

Both model providers are handed the **same JSON schema** — Anthropic as
`output_config.format`, vLLM as `guided_json` — so moving between a frontier
model and a local one changes the runtime, not the answer contract. With
nothing configured the pipeline is fully deterministic, which is what keeps CI
and every eval run offline.

```bash
LLM_PROVIDER=anthropic ANTHROPIC_API_KEY=sk-... make console
LLM_PROVIDER=vllm VLLM_BASE_URL=http://localhost:8000 VLLM_MODEL=llama-3.1-8b-instruct make console
```

Two things guard the rewrite, and they catch opposite failures. A figure the
model **invents** is caught by `numeric-binding`, which blocks any digit that
traces to no row. A figure it silently **drops** would slip past — an answer
with no numbers has no unbound numbers — so a rewrite that loses a figure the
composer had already placed is rejected before the gates run, and the
deterministic wording is kept. Either way the trace records what happened, and
a provider that is down or throttled degrades to the composer's prose rather
than failing the question.

**Graph traversal replaces multi-hop RAG** (§E.1). "Is my pre-existing condition
covered?" loads the product page, follows `links.exclusions`, then
`concept/pre-existing-condition`. Three deterministic reads, complete exclusion
set guaranteed.

**The assistant audits the websites** (§D.2). `make conflicts` compares every
raw source against the benefit tables under the declared authority order. The
seed bundle ships with a planted disagreement — one front door says the delay
benefit starts after 4 hours; the tables say 6 — and it is filed as a *website
defect ticket*, not a wiki problem. Two addresses of the same channel quoting
different limits is a bug on the website, never a product difference.

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
fixtures/               the synthetic two-host site the crawler is proved against
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
| channel-coherence | the render disagrees with the session, or offers another *route's* contact |
| exclusion-completeness | coverage is asserted without the exclusion page having been read |
| advice-boundary | advice is sought or the product is regulated, and no adviser handoff |
| groundedness | a claim is not entailed by the pages actually loaded |

## Guardrails (§F.4)

The gates are exact and cannot read. They prove a figure came from a named
table row and that contact details belong to the session's channel; they cannot
tell that a fluent, accurate answer about delay thresholds is a non-answer to
"how much does it cost a year", or that "what cover do you recommend I take" is
the same regulated request as "which plan should I buy". Those need a reader,
so the loop is bracketed by two screens.

```
guardrail-input → retrieve → compose → generate → guardrail-output → gates
```

Each screen is two layers, and they are weighed rather than simply combined:

| Layer | Runs | Strong on | Contributes nothing to |
|---|---|---|---|
| rules | always, not switchable | injection, impersonation, third-party requests, distress | advice, off-topic, groundedness |
| model | when one is configured | advice however phrased, off-topic answers, ungrounded claims, leakage | — |

A flat "worst verdict wins" throws away the thing that decides quality here:
the two layers are good at different categories by very different margins. The
rule layer fired on **none of 5,434 legitimate questions**, which is what earns
it the authority to block `injection` alone; it contributes nothing at all to
`advice`, where it does not even try. So each category carries a policy — what
each layer's word is worth, what it takes to flag, what it takes to block:

```python
"injection": Policy(rules=1.0, model=0.9, flag_at=0.35, block_at=0.8)
"advice":    Policy(rules=0.0, model=0.7, flag_at=0.3,  block_at=None)
```

`block_at=None` means the category may never block, whatever either layer says.
That is a product decision written where it belongs: an advice request must
reach an adviser and a customer in distress must reach a person, and neither is
served by a refusal. The same category is weighed differently on each side —
incoming `advice` is a request to route, outgoing `advice` is the breach itself.

Scores combine with a noisy-OR, so agreement between the layers counts for more
than either alone and no amount of piling on manufactures certainty. Two scores
are kept per category: `block_score` counts only sources that proposed a block,
so any number of flags stays a flag and a cautious model cannot refuse a
customer by degrees.

**A model verdict may raise the risk of a turn and may never lower it.** Once
verdicts are weighed rather than maxed, that stops being obvious — so it is
structural rather than a rule someone has to remember. The combiner is monotone
in every input: adding evidence can only raise a score, so no arrangement of
model output pulls a turn below what the rules alone decided. A test asserts it
over every subset of a finding set.

Flags do something. An input screen that reaches `advice` routes the turn
through the adviser handoff — closing the gap the eval measured, where the
keyword classifier catches "which plan should I buy" and misses "what cover do
you recommend I take".

A refused turn ends before retrieval: no page budget, no SOR call, no model
call, and nothing carrying instructions ever reaches a prompt. The refusal
itself does not name the rule it tripped — that detail goes to the trace, where
an operator can read it and a probe cannot.

Both screens report as gate results, so one list decides whether an answer
ships and the console, the trace and the eval harness need no special case. The
trace carries the arithmetic — `injection=block(1.00/1.00)` — because a refusal
an operator cannot account for is one they cannot tune. A model that is
configured but silent is recorded as `degraded` rather than read as clean;
`GUARDRAIL_FAIL_CLOSED` decides whether that stops the turn.

Screening shares the answering provider and its credentials. There is no
separate guardrail key: setting `ANTHROPIC_API_KEY` turns on the model layer for
both screens as well as the answer, and there is no arrangement of settings that
leaves a turn screened by rules alone while Claude writes the answer. One
provider instance serves the whole turn, so the client and its connection pool
are shared rather than rebuilt.

The cost of that is worth stating plainly: **a fully configured turn makes three
model calls, not one** — screen the question, write the answer, screen the
answer. Across the 4,824-case eval suite that is ~14,500 calls rather than
~4,800. `GUARDRAIL_MODEL` points the two screening calls at a different model
without touching the credentials; screening is a shallow judgement on the
request path, so `claude-haiku-4-5-20251001` is usually the right trade against
the model writing the answer. `GUARDRAILS=rules` turns the model layer off
entirely and keeps the deterministic floor.

Calibration lives in `apps/api/tests/guardrail-scenarios.yaml` — 113 labelled
turns, 69 benign to 44 hostile. The benign side is the larger one deliberately:
insurance language is full of the words a naive filter reaches for, and the
first draft of these patterns blocked "show me the rules", "does my wife act as
an additional driver" and "I want to end my life insurance policy".

## Docker

```bash
make docker-up      # build and start; console on http://localhost:8080
make docker-logs
make docker-down
```

One service, because that is what the system is: a single FastAPI process
serving the console, the studio and the API off a directory of Markdown and
CSV. No database, no cache, no queue — the trace store is an in-memory ring
buffer and the system of record is a fixture. The image is ~264 MB, runs as a
non-root user, and mounts the bundle read-only.

The compose file lives at `infra/docker-compose.yml` and the Makefile targets
pass `--project-directory .`, which is what makes the repo-root `.env` the
source of settings and the relative volume paths resolve from the repo root.
With no `.env` present every value falls back to a default and the loop runs
deterministically.

**A local model is deliberately not a service.** On Apple Silicon it runs on
Metal, which Docker Desktop does not pass through to containers — a
containerised MLX server falls back to CPU and is unusably slow. Run it on the
host and point the container at `host.docker.internal`:

```bash
mlx-openai-server launch --model-type lm --port 8090 \
  --model-path mlx-community/Qwen3.6-35B-A3B-OptiQ-4bit \
  --served-model-name qwen3.6-35b-a3b

VLLM_BASE_URL=http://host.docker.internal:8090 \
VLLM_MODEL=qwen3.6-35b-a3b GUARDRAIL_MODEL= make docker-up
```

On a Linux host with NVIDIA GPUs, vLLM in a container is the right answer and
belongs in the compose file as a second service.

## Configuration

Everything external is optional — with nothing configured the loop runs
deterministically, which is what keeps the console and the eval suites usable
offline.

Copy the template and fill in what you need — there is no `.env` in a fresh
checkout because nothing requires one:

```bash
cp .env.example .env
```

`.env` is gitignored and is read from the working directory, so it belongs at
the repository root and `make` should be run from there.

```
BUNDLE_PATH=okf
LLM_PROVIDER=auto       # auto | deterministic | anthropic | vllm
ANTHROPIC_API_KEY=      # unset → deterministic composer
ANTHROPIC_MODEL=claude-sonnet-5
VLLM_BASE_URL=          # a locally hosted model, same output contract
VLLM_MODEL=
GUARDRAILS=auto         # auto | rules | off — the rule layer always runs
GUARDRAIL_MODEL=        # defaults to the answering model; see the note below
GUARDRAIL_FAIL_CLOSED=false
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
