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
| **RAG** — the long tail and the raw source of truth | `apps/api/api/retrieval.py` — hybrid fallback over `okf/raw/` |
| **Harness** — what makes it safe to answer at 2am | `packages/harness` — contracts, eight gates, budgets, traces |

Retrieval itself is three layers, and the point is that each closes a failure
the other two cannot — see [DESIGN-v2.3.md](DESIGN-v2.3.md):

| | Answers | Closes |
|---|---|---|
| **Graph** — `packages/okf/okf/graph.py` | "what points at what" — typed edges, containment, both directions, deterministic walks | an incomplete exclusion set, which is how an answer becomes wrong rather than short |
| **OKF** — `packages/okf` | "why this page exists" — approved, dated, jurisdiction-bound, with its authority order | a stale or unapproved page reaching a customer |
| **Vectors** — `apps/api/api/vectors.py` | "the thing I can't name precisely" — dense recall over the wiki *and* the raw sources | a question phrased in the customer's words rather than the contract's |

None of the three is a bypass. A page found by similarity is a candidate under
the same frontmatter filter, the same composition and the same gates as one
found by words; a section found in `raw/` by similarity is admitted by the same
marketing screen and version filter as one found lexically.

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

To serve the real Etiqa/Tiq corpus in a container instead:

```bash
BUNDLE_PATH=okf-real make docker-up
curl -s http://localhost:8080/readyz     # {"status":"ready","pages":768,...}
```

Three step-by-step guides sit alongside this README. Each was written by
running it, and every output they quote is what the command printed:

| | |
|---|---|
| **[DEPLOYMENT.md](DEPLOYMENT.md)** | serve it — configure, start, verify in four ordered checks, operate, and what it is not ready for |
| **[CORPUS.md](CORPUS.md)** | build the corpus — crawl, parse the PDFs, read the FAQs, compile, lint, review |
| **[EVALUATION.md](EVALUATION.md)** | measure it — six suites, what each tests, and how to read a failure |
| **[DESIGN-answering.md](DESIGN-answering.md)** | a proposal, not yet built — why selection is the weak half and what would replace it |

The rest of this README is the design: what the system is and why it is shaped
this way.

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
- **eight gate verdicts**, and the blocked draft when one refuses delivery
- the **frontmatter filter**: pages admitted *and pages rejected with the reason*
- **graph traversal**: which pages were reached, by which typed edge, and at which hop
- **RAG fallback**: whether it fired, why, and whether each hit was found by words,
  by similarity, or by both
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

**Ingesting a wording is not reading one.** For a while this repo did the
first and called it the second: the two highest-authority tiers were full,
the manifest declared them highest-authority, and every compiled page was
still built from marketing HTML. It showed — 108 of 108 exclusions pages said
the exclusions could not be extracted, while the contract that lists them sat
unread on disk. `compiler/documents.py` closes that gap, and the work is
almost entirely in recovering structure the PDF layer destroyed:

* paragraphs are **rebuilt**, because a line break inside a sentence is an
  artefact of page width — and one backend emits 2,470 lines with 89 blanks,
  so blank lines cannot be the only separator;
* folios and revision stamps are **dropped before** headings are looked for.
  `V1.25` is short and title-cased, so it passes any shape test for a heading,
  and letting it through opens a new section at every page foot: 110,000 words
  of one policy contract filed themselves under a heading called "v1.25";
* headings are **classified, not indexed**. "General Exclusions", "What is not
  covered" and "Section 7 — Exclusions applicable to all sections" are one
  role, and the role decides which page the text lands on. So does "What do we
  mean with these words?", which is 40,000 words of definitions on this corpus;
* campaign paperwork is **excluded**. The ingest tiers by filename and an
  insurer names both its contracts and its lucky draws "terms and conditions",
  so ~45 promotional documents arrive filed as wordings. An offer that expired
  in 2024 is not policy terms.

Where a product already has a crawled page, the contract's sections are
written **above** the website's on the same page and both keep their own
references: authority becomes page order, and composition reads from the top.
Where it does not — commercial fire, contractors' all risks, fidelity
guarantee, the whole rider range — the document *is* the product page. That is
a third of the book that previously retrieved nothing at all.

### Numbers in a contract

Rule 2 says numbers never live in prose; they come from benefit-table rows. A
policy wording breaks that rule on every page, and neither escape works: a
notice period is not a benefit, so it has no row, and paraphrasing it away
changes what was agreed. The third option is to **quote** — reproduce the
clause verbatim, name the document and printed page it came from, and mark it
as reproduced rather than written:

```markdown
> You must notify Us within thirty (30) days of the event.
> [src:raw/wordings/tiq-home-policy-wording.md#p7]
```

A figure lifted from a quotation binds to that locator instead of to a row,
and `numeric-binding` **re-opens the document and looks for it**. A quotation
the source does not contain fails the gate exactly as an invented number does,
which is what stops "it was a quote" from becoming a way to assert anything at
all. This is why `raw/` ships with the wiki: without the sources, every quoted
figure is unverifiable, and an unverifiable claim of verbatimness is not a
binding.

The same path now carries the published FAQs, which are also somebody else's
words reproduced rather than the compiler's own. That change alone took the
real bundle from 320 lint errors to zero.

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

### What a local model actually costs

Qwen3.6-35B-A3B at 4-bit on Apple Silicon, against the deterministic composer,
same 604-case suite and same 100-conversation suite:

| | deterministic | Qwen3.6-35B-A3B (4-bit) |
|---|---|---|
| accuracy | 95.0% | **94.2%** |
| citation F1 | 0.958 | 0.958 |
| figure exact match | 96.9% | 96.9% |
| numeric binding | 100.0%, 0 unbound | 100.0%, 0 unbound |
| entitlement leaks | 0 | 0 |
| merge consistency | 6/6 | 6/6 |
| recall@1 / @3 / MRR | 0.59 / 0.97 / 0.82 | 0.59 / 0.97 / 0.82 |
| conversations (whole / turns) | 96.0% / 98.8% | 96.0% / 98.8% |
| latency p50 / p95 | 3.9 / 4.3 ms | 3,342 / 5,331 ms |

Everything except accuracy and latency is **identical**, and that is the point
rather than a coincidence: none of those rows is the model's to decide.
Retrieval picks the pages, the transclusion pass resolves each figure against a
benefit-table row or a verified quotation, and the model is handed prose to
write. Three orders of magnitude of latency buys a different runtime, not a
different answer contract.

The accuracy difference is exactly five cases, and they are all the same shape:

    qwen only   gap-*-document, gap-*-document-1, gap-*-premium ×2,
                gap-*-premium-1, gap-*-renewal
    det only    gap-*-buy-1

Every one is a `gap-*` case — a question the corpus genuinely cannot answer
("How much does Travel Insurance cost me a year?"). The expected behaviour is a
handoff. The deterministic composer refuses flatly; Qwen writes something
fluent around the hole and never sets `handoff`. Nothing unbound or leaked got
through — the gates saw to that — but a non-answer delivered as an answer is
the softer version of the same failure, and it is the one a local model
introduces.

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

The walk is *ordered by what the question asked for* and never *filtered* by
it: a claims question follows the `claims` edge first, and still follows the
exclusions edge, because the completeness gate will refuse a coverage
assertion made without it. Containment is an edge too — the real corpus has
150 `/cover`, `/definitions` and `/eligibility` pages that no `links:` block
points at — and edges run backwards as well as forwards, so a turn that landed
on a concept page can reach the product that owns it. Every loaded page records
which edge produced it.

**Dense recall is recall, not a shortcut.** The vector index is built one row
per section, and both halves of it are used: pooled to page scores to decide
*which pages* to read, and kept at section level to help decide *which section
of them* answers. The RAG fallback searches the raw sources the same two ways
and fuses the rankings by reciprocal rank — a section both retrievers place
second beats one that either places first, because a share-of-information
ratio and a cosine similarity are not on the same scale. With no index
configured the fallback is byte-for-byte the lexical one it has always been.

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
okf-real/               the Etiqa/Tiq corpus — COMMITTED, and what a deployment serves
  raw/wordings/         168 policy wordings and contracts, parsed from PDF
  raw/product-summaries/  52 regulated product summaries
  raw/web/              dated crawl snapshots of both hosts
  raw/faq/              published FAQ pairs (WordPress REST, tiq.com.sg)
  wiki/                 754 compiled pages · `make corpus-compile` regenerates
okf-web/                build output: crawled + compiled. `make knowledge` regenerates it
fixtures/               the synthetic two-host site the crawler is proved against
packages/okf/           page model, frontmatter schema, tables, graph, linter, corpus IDF
  graph.py              typed edges, containment, reverse index, deterministic walks
packages/harness/       contracts, eight gates, budgets, traces
apps/api/               serve loop, debug console, content studio, content API
apps/crawler/           allowlist + robots policy, extraction, dated snapshots
apps/compiler/          snapshot → wiki compile, fact extraction, conflicts, impact
evals/suites/           golden · merge-consistency · adversarial · staleness ·
                        field-test · faq-customer · conversation (generated)
evals/taxonomy/         the authored conversation taxonomy: what customers ask,
                        and what a correct reply is for each
```

## The corpus in this repo

`okf-real/` is committed, sources and all, because a deployment that has to
crawl two websites and parse 300 PDFs before it can answer anything is not a
deployment. `BUNDLE_PATH=okf-real` and it serves.

| | |
|---|---|
| wiki pages | 754 |
| products | 108 from the websites, plus those that exist only as a PDF |
| policy documents compiled | 194 (220 ingested, 45 of them campaign paperwork) |
| pages citing a contract | 482 |
| bundle linter | 0 errors, 0 warnings |

Measured on it, deterministic composer, 25,791 auto-generated cases:

| | |
|---|---|
| accuracy | 82.3% |
| citation F1 | 0.837 |
| figure exact match | 79.5% |
| **numeric binding** | **100.0% — 0 unbound, 0 leaks** |
| recall@1 / @3 / MRR | 0.79 / 0.97 / 0.91 |
| latency p50 / p95 | 118 / 517 ms |
| corpus reach | 97.5% of pages, 88% of table rows |

The gap between 82.3% here and 95.0% on the seed bundle is **figures, not
text**. 4,563 cases fail and they are overwhelmingly `fig-*`: 10 of 108
products have a benefit table, 73 rows in total, because the compiler only
lifts a table it can recognise as a schedule of benefits and most of these
products publish theirs as a PDF layout rather than an HTML table. Compiling
the wordings fixed what a product *says*; what it *pays* is still thin.

That numeric-binding row is the one to read twice. Across 25,791 cases on a
corpus where 499 pages quote a contract, not one number reached an answer
without a table row or a verified transcription behind it.

The sources ship with the pages and are not optional: `numeric-binding`
re-reads a wording to verify every quoted figure, and the RAG fallback greps
`raw/` for questions no compiled page answers. Serving `wiki/` alone would
refuse every answer that quotes a contract.

**These pages are signed off `UNREVIEWED-eval-only`, and that is literal.**
The compiler writes pages as `draft`, and a draft page is invisible to
retrieval and fails `reference-integrity` — a freshly compiled bundle answers
nothing, deliberately, because compiled-from-a-crawl is not the same as fit to
say to a customer. Two things promote a page: a person reading it in the
studio and flipping its status, or `--sign-off`, which stamps the whole bundle
at compile time and records who claimed it. `make corpus-compile` uses the
second, with a name chosen to be impossible to miss in the frontmatter.
Nobody has read these pages. That is a decision to make, not a step to skip.

## Multi-turn

A hundred generated conversations, 325 turns, eight archetypes — a customer
exploring, a customer correcting themselves, an attacker mid-conversation,
someone asking for advice at the end. Run against all three bundles:

| | seed (3 products) | fixture (22) | real (108+) |
|---|---|---|---|
| whole conversations | 96.0% | 56.0% | 46.0% |
| turns overall | 98.8% | 74.8% | 67.4% |
| standalone turns | 100.0% | 85.4% | 79.4% |
| context-dependent turns | 96.8% | 57.9% | 48.4% |
| self-contradictions | 0 | 0 | 0 |
| attacks held | 24/24 | 24/24 | 24/24 |
| answered the next turn | 12/12 | 12/12 | 10/12 |

**Nothing the customer said earlier is carried.** The session holds channel,
auth level and policy context; the question text does not accumulate. That is
what the context-dependent row measures, and the three columns show the shape
of the problem clearly: an elliptical follow-up — "and the premier tier?" —
retrieves on the fragment alone, which lands on the right product when there
are three of them and lands anywhere when there are a hundred. The seed
bundle's 96.8% is not the system resolving reference; it is a corpus small
enough that failing to resolve it does not matter.

What holds across all three is what the gates own: no conversation ever gave
two different figures for one fact, and every attack was refused mid-thread
without the bot then punishing the customer by staying refusing.

## The eight gates (§F.2)

| Gate | Blocks when |
|---|---|
| reference-integrity | a cited source is missing, unapproved or out of window |
| numeric-binding | a number in the answer traces to no table row or SOR field |
| version-coherence | cited pages mix versions, or contradict the in-force policy |
| channel-coherence | the render disagrees with the session, or offers another *route's* contact |
| exclusion-completeness | coverage is asserted without the exclusion page having been read |
| advice-boundary | advice is sought or the product is regulated, and no adviser handoff |
| groundedness | a claim is not entailed by the pages actually loaded |
| answerability | nothing loaded could settle the question that was asked |

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
PGVECTOR=auto           # auto | on | off — `auto` with no DSN is lexical only
PGVECTOR_DSN=           EMBED_BASE_URL=          RERANK_BASE_URL=
VECTOR_FLOOR=0.55       VECTOR_RAW_FLOOR=0.5
```

## Status

Built: the OKF bundle contract and linter, wiki-first retrieval with typed,
question-ordered graph traversal, deterministic numeric binding, a hybrid
lexical + dense RAG fallback over `raw/`, the SOR
entitlement stub, all eight gates, budgets, full tracing, the debug console,
conflict detection with impact analysis, the four eval suites wired to a CI
gate, a 1,356-case golden conversation dataset over all 37 products,
the allowlisted crawler, the compile step that turns crawl snapshots into
canonical pages, benefit-table CSVs and website defect tickets, and the content
studio — review, scan-and-verify, authoring, status workflow, tagging,
in-process evaluation and the integration registry.

Not built:

- **Routing a question the corpus cannot answer.** The largest finding of the
  golden conversation dataset (`make conversation-eval`, EVALUATION.md §4a):
  of 191 cases whose correct reply is "this needs a system or a human",
  82 got a substantive answer instead. "I received an OTP I did not request"
  was answered with a Start-up Bonus rate table; "is this email really from
  you?" with a policy termination clause. Every gate passed — they check that
  an answer is grounded, not that the question was one to take. The corpus
  cannot hold a claim status, a password reset or a phishing verdict, and the
  bot needs to say so rather than retrieve the nearest clause.
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
- **pgvector dense retrieval — present, off by default, and unmeasured.**
  `§J.1` recommended grep + frontmatter filter until measured recall degraded.
  It did: the field test found the lexical scorer tying 87–213 pages on
  ordinary customer questions, and "my flat got flooded" reaching a different
  product from "my home got flooded". v2.1 added a pgvector index over the
  compiled wiki sections as a recall layer only — fused into the lexical rank
  before the frontmatter filter runs, so it cannot admit what the filter
  rejects. v2.3 uses the index at the level it is built at (a section hit now
  steers section selection, not just page ranking) and adds a second table
  over `raw/`, which makes the RAG fallback hybrid. All of it behind
  `PGVECTOR=auto|on|off`; see DEPLOYMENT.md §8a and DESIGN-v2.3.md.

  What is *not* done is measuring it. Both floors and the section weight are
  settings rather than constants because the numbers that should set them come
  from running the field test and the FAQ suite against a live index, which
  needs the GPU box. The suites in this repo run without one and so cannot
  move on this work — they show it costs nothing, not that it earns anything.
- **Conversational memory.** A session carries channel, auth and policy;
  it does not carry what was said. Measured cost above — context-dependent
  turns fall from 96.8% to 48.4% as the corpus grows from 3 products to 108,
  because an elliptical follow-up is retrieved on its own words. The
  conversation suite exists to keep that number honest while it is unfixed.
- **Benefit tables for most of the real corpus.** 10 of 108 products have
  one. This is the single largest source of failure on `okf-real` and it is a
  document-extraction problem, not a retrieval one: the schedules are PDF
  layout, not HTML tables.
- **LLM-widened paraphrasing** in the generator. Question phrasings are
  template-derived; a configured endpoint could widen them, and the
  deterministic templates stay the floor.
