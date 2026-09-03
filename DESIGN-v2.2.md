# v2.2 — One reading of the question

Branch `claude/genai-insurance-chatbot-v2.1`, from `c2b0d88`. Companion to
[DESIGN-v2.1.md](DESIGN-v2.1.md), which this does not replace: the safety
architecture there — the model phrases, never establishes a fact; nine gates
verify afterwards — is unchanged. v2.2 adds rigour on the other side of the
turn, where none was.

## Why

On 2026-09-03 four customer-shaped questions failed in front of the product
owner, in four different ways:

| Typed | Bot did | Where the defect lived |
|---|---|---|
| tiq travel | handed off | corpus: the flagship's only alias was `travel`; "Tiq Travel Insurance" was never recorded |
| how to buy? | recited travel cover | router: no requirement that a purchase question be answered from a channel page |
| Tiq travel insurance coverage | 55% promo, then 1,900 words of definitions | corpus: no plain-language cover section existed; composer: the summary regex needed a question word |
| tiq travel coverage | asked which of three travel products | router: names matched titles only; the family test stripped the brand and found six |

Every one was already in the corpus. Every fix was a new special case, and
three of them broke a passing case before the suite caught them. The answer
side had nine gates; the question side had a pile of regular expressions.
That asymmetry is what v2.2 closes.

## The Ask — `packages/harness/harness/ask.py`

One typed reading of the question, built once per turn before retrieval and
recorded on the trace as the `ask` stage:

```
Ask
  product / product_page   the product this turn is about, once known
  named_by                 title | alias | history | flagship | model | ""
  family / family_phrase   page ids the phrase could mean, where it means several
  intent                   the harness classifier's verdict
  subject                  benefit codes the question names (vocabulary, "section 6")
  scope                    overview | specific
  kind                     product | general_insurance | off_topic  (from the model)
  ambiguous                the reading could not choose
  evidence                 per field, what set it — read on the trace
```

Filled in order, and the trace says which source set each field:

1. **The product-name index** (`packages/okf/okf/names.py`). Titles and
   aliases, the shopfront's name included, matched longest-first with
   subsumption: "tiq travel covid" absorbs the "tiq travel" inside it. One
   product counts once however many of its names the customer used. A phrase
   that names no product but sits inside two or more titles is a *family*;
   a member whose title is the phrase itself is its *flagship* and answers
   for it. A question that is one of the product's names and nothing else
   (filler aside) is an `overview` request.
2. **The deterministic intent classifier**, unchanged.
3. **The model**, only for what those left open — the existing understand
   stage, now skipped whenever the index resolved the product, and unable to
   overrule a product the customer named.

Readers converted: `named_products` and `product_family` in retrieval,
`select_sections` and `compose` (which took `ask`), the answerability gate,
and the clarify decision in the pipeline. Deleted: the titles-only name
match, the brand-stripping branch, `NAMES_ONLY_RE` as an overview signal,
`bare_product_name`, and the two coverage regexes in the composer (they live
in `harness.ask` now, one definition).

**A tenth gate, `about-the-ask`.** Where the customer named the product, the
answer's cited product must be that product. The v2.1 metric counted these
as `wrong_product`; a count is now a refusal. It skips on handoffs,
clarifications, smalltalk, and on a product the model inferred — refusing on
a guess the customer never made would punish the wrong party.

**Clarify policy.** Named → answer, never ask. Family with a flagship →
answer the flagship. Family without one ("cancer insurance" inside two
titles) → ask, with the members as chips. Two full names in one turn → ask.
Nothing → ask which product, as before.

## Streaming — `POST /v1/answer/stream`

Server-sent events, same request body as `/v1/answer`. What is streamed
follows from the safety line rather than crossing it: a draft streamed token
by token would show text the entailment judge or the figure check may then
refuse, and an insurance answer that appears and is retracted is worse than
one that arrives whole.

```
event: stage   {"name","phase":"start"|"end","label","ms"}   as each stage opens and closes
event: delta   {"text"}                                       the VERIFIED answer, paced in 3-word chunks
event: done    <the /v1/answer envelope>
event: error   {"detail"}
```

Only stages with a customer label are emitted (`STAGE_LABELS` in
`main.py`). The chat page reads the stream by hand — `EventSource` is
GET-only — shows the label under the typing indicator, appends deltas, then
renders the envelope exactly as before. Nothing shown is ever taken back.

`Trace.listen()` is the hook: a `(name, phase, ms)` callback the pipeline
calls from `trace.stage()`. Nothing else in the pipeline knows it is being
streamed.

## The catalogue — `okf-real/catalogue.yaml`

The crawl labelled a page "product" from its URL, and that made 156 products
out of a catalogue of thirty-eight: every business sub-category stub
(Burglary, Office, Retail, Plate Glass…), four life-stage landing pages under
Term Life (Family, Single, Just Married, Loans), category indexes
(Investments, Motor Insurance, Premier Solutions), a webinar, a fire-safety
event, a survey form. A customer who typed "tiq home" was asked whether they
meant the event or the webinar.

The owner supplied the catalogue on 2026-09-03 — 35 current products and 3
legacy ones, with their official pages — and it is now data the compiler
obeys. One product per entry, built from the listed pages and from the
documents whose names match the entry's keys. A crawled page the catalogue
does not list is not a product, whatever the crawl called it. A page shared
by several entries (the Life & Critical Illness category page) is attached
to none. Legacy entries compile with `lifecycle: closed_to_new_business` and
the composer opens their answers with "closed to new customers".

Result: **38 product pages**, every catalogue name resolves ("tiq home" →
Tiq Home Insurance, "eprotect maid" → Tiq Maid Insurance, "3 plus ci" → Tiq
3 Plus Critical Illness), no clarification on a named product. Deterministic
FAQ suite 354 / 364; field test 80 / 109 on the deterministic path (the
remaining hold cases — "my house", "my helper", advice-switching — resolve
through the model).

**Documents the catalogue does not claim.** The compiler now reports these
by name and compiles nothing from them. They are for the owner to map — each
is either a product missing from the catalogue, a rider that belongs to a
listed product, or paperwork:

| group | plan names in `raw/` |
|---|---|
| CI riders | direct-etiqa-ci-rider, direct-etiqa-ci-rider-ii, advanced-ci-rider, ci-benefit-rider, ci-protection-rider, early-ci-rider, early-ci-benefit-rider, early-ci-protection-rider, etiqa-direct-critical-illness-rider, heart-neurological-disorder-rider (×2) |
| Waivers / care riders | extra-secure-waiver (I, II), extra-payer-waiver (I, II), extra-disability-care (+ rider), extra-cancer-care-waiver |
| Enrich series | enrich-assure, enrich-flex-plus, enrich-goal, enrich-income, enrich-retirement, enrich-rewards, enrich-saver |
| Invest series | invest-flex-prime-ii, invest-flex-pro, invest-flex-wealth-ii, invest-plus-sp, invest-prime-purpose, invest-smart-flex-ii, invest-wealth-purpose, invest-starter |
| Other products not listed | elastiq, dash-easyearn (+ lite), dash-pet, gigantiq-sprint, gigacover-flip, singtel-bill-protect, flep, travel-pass, eprotect-mortgage, eprotect-family, essential-lifetime-secure, essential-critical-secure, accidental-death, death-tpd, life |
| Home variants | complimentary-home, eprotect-home, etiqa-eprotect-home, etiqa-homeowners-enhanced, home-renewal-protection-bundle, etiqa-fire |
| Business documents | etiqa-autolab-package-wic, etiqa-management-corporation-errors-ommission |

Published FAQs with no product to hang on: GIGANTIQ (37 pairs), ELASTIQ (33),
Dash PET (45), Dash EasyEarn (16).

Two things this changes for the measurement: corpus reach by sentence falls
to 5% (the unclaimed documents are most of the words), and the 1,000-case
sample in `.eval-reports/real-sample-1000.json` was generated from the old
156-product corpus and must be regenerated before the next run.

## Sources — the product page and the documents, never the marketing

The corpus holds 1,001 crawled pages, and 586 of them are blog posts; press
releases, awards pages and tag indexes make up most of the rest. A blog post
about choosing travel insurance is not the insurer's statement of what the
policy covers. `packages/okf/okf/sources.py` draws the line once:

| class | what | may support a claim |
|---|---|---|
| document | `raw/wordings`, `product-summaries`, `brochures`, `faq`, `benefit-tables` | always |
| product page | crawled pages typed `product`, `claims`, `faq`, `servicing` | always |
| offer | crawled pages typed `promo` | only when the customer asked about an offer |
| marketing | `blog`, `other` (about-us, awards, tag indexes), press releases | never |

Enforced in three places, so a marketing sentence cannot reach a customer
by any path:

- the raw-corpus fallback search skips marketing files (it walked every
  file under `raw/` before);
- the compiler chooses concept and channel sentences from product pages
  only (a blog post defined "excess" before), and promotion pages are
  admitted to retrieval only for an offer question;
- an eleventh gate, `supporting-sources`, classifies every claim's source
  after composition and refuses on marketing.

Two corpus fixes fell out of checking Home Insurance against
`tiq.com.sg/product/home-insurance`:

- the etiqa.com.sg site also lists the product on a post-application page
  whose only section is 419 words of marketing-consent terms; the duplicate
  fold had preferred the longer listing, and "Marketing Consent Terms &
  Conditions" became what the plan is. The canonical listing stands now.
- the tiq.com.sg home page carries no benefit tiles, only "Why Tiq Home
  Insurance?" and a promotion, so its "What it covers" was a slogan. Every
  product page with a compiled cover page now also carries the wording's
  sections of cover, cited to the wording — "Building; Renovation;
  Emergency Cash Allowance; Personal Legal Liability; …" — on 37 products.

And an `offer` intent: "is there a promo for travel insurance" is answered
from the promotion pages, which now carry the product they are for; where
there is none, the answer says so instead of quoting a policy clause about
other insurance. The no-claim discount is not an offer.

## Measurement

**`evals/suites/faq-customer.yaml`** — 395 questions across 19 products,
generated by `scripts/faq_suite.py` from `raw/faq/*.md`: every one asked by
a real customer in their own words and answered by the insurer on its own
site. Expectation: delivered, citing the FAQ's product. The 318 questions
that name no product on their own ("How can I benefit from this plan?") are
two-turn conversations, product first, and the expectation applies to the
last turn. `make customer-suite` runs it with the field test; `make
faq-suite` regenerates it after a compile.

Deterministic runs, in order:

| build | passed | what changed |
|---|---|---|
| Ask landed | 332 / 395 | — |
| + product carried from the earlier turn | 349 / 395 | the two-turn cases resolve without a model |
| + duplicate products folded at compile time | 380 / 395 | 29 failures were one corpus defect |
| + the same fold on the wordings path | **382 / 395** | no product compiled twice remains |

The corpus defect: the same product listed under two URL slugs — etiqa.com.sg
lists Home Insurance twice, and spells the investment product `tiq-invest`
where tiq.com.sg spells it `tiqinvest`. Grouped by slug, six products were
compiled twice, and a customer asking about one was answered from the other.
`merge_duplicate_groups` in the compiler folds two listings whose titles
agree once the brand and the category word are taken off — four pairs on the
web path — and the same identity rule attaches a wording to the web product
it belongs to (`elastiq` → `universal-life-insurance-elastiq`) and folds two
filings of one rider into one. No product is compiled twice now.

Of the 13 still failing deterministically: four cite no product, three are
not delivered, and the rest are single cases. The
live run on Qwen is queued behind the baseline (`.eval-reports/v22-baseline/
customer-suite.log`).

**`okf-real/coverage.json`** — per raw source, words in and words a wiki
sentence cites, written by every `corpus-compile` (`make coverage` on its
own). Attributed by sentence, so stricter than the file-level ratio in the
plan: **10%** of the corpus reaches a page. The top of the list:

| reached | unreached words | source |
|---|---|---|
| 0% | 25,629 | travel-infinite-covid-19-policy-wording-2025-03 |
| 0% | 24,444 | tiq-travel-covid-19-policy-wording |
| 7% | 22,705 | policy-contract-for-early-ci-protection-rider-v1-23 |
| 9% | 22,546 | policy-contract-for-early-ci-rider-v1-23 |

Two whole Covid wordings reach nothing. That is the list a human reads
first.

**Baseline on the current build** (plan step 1): `.eval-reports/v22-baseline/`
— the field test and the 1,000-case sample on live Qwen, at commit
`c2b0d88`, before the Ask landed. Numbers are appended here when the run
completes.

## Four decisions from the owner (2026-09-03, evening)

**The product page and the documents it links are the reference.** Where
sources disagree, the product's own page wins. A Tiq product's page is on
tiq.com.sg and an Etiqa product's on etiqa.com.sg, so that host ranks first
for the product's benefit table and its opening line. A figure stated on the
product page ("travel delay cover from just 3 hours") is quoted from the page
and bound to it — the same mechanism a promotion's figures use — rather than
dropped because a schedule elsewhere says six. The conflict tickets in
`okf-real/conflicts/` remain the list of places the *website* disagrees with
itself; the answer follows the product page.

**Names bend.** The catalogue slug is whatever the owner writes (`tiq-invest`
now). Every name a customer uses is an alias. A misspelt name still names the
product: a run of the customer's words within a small edit distance of
exactly one product's name — "tiq travle", "maid insurence", "pet insurnce",
"tiq hom" — is read as that product, marked `fuzzy` on the trace, and never
when two products are within reach of the same slip.

**LLM WIKI drafts are cross-checked against the product page automatically.**
After provenance is proven (the cited section exists, the figures are in it),
every sentence is put to the same entailment judge the groundedness gate
uses, against the product-page section it cites. Not entailed → dropped. A
page whose every sentence was entailed is written `approved` with
`auto-crosscheck` as the reviewer; anything less stays a draft. The first
generation on the 38-product corpus is queued behind the baseline
(`.eval-reports/v22-baseline/llm-wiki.log`).

**GPU deployment, in plain terms.** Today the model (Qwen), the API and the
evaluation all run on this Mac, which is why a turn takes 4–30 seconds and
why the evaluation slows down when anything else runs. Plan step 7 is to run
the same software on a Linux machine with a graphics card: the model in
vLLM, the vector database in Postgres, the embedder in TEI. The configuration
for that is already in `infra/docker-compose.yml` under the `gpu` profile
and `.env` switches the URLs; nothing in the code changes. It is optional —
the bot works without it — and it only matters when the wait per answer
matters. If there is no GPU machine, skip it.

## The four enhancements (2026-09-03, night)

**Personal mobility → Tiq Personal Accident.** The catalogue entry is gone;
its names are aliases of Tiq Personal Accident and the entry carries
`replaces: [ePROTECT personal mobility]`, so a customer asking about the old
product is told, first, that Tiq Personal Accident replaced it.

**1. A presentation layer** — `apps/api/api/present.py`. The composer
establishes facts and the gates verify them; neither cares how the result
reads, and "tiq home" came back as the wording's section list, then the
site's intro, then a stray FAQ answer, then a link. For an introduction
(`Ask.scope == "overview"`) the same verified sentences are now reordered
and labelled: an opening line, "What it covers" as a list, the route to buy,
and a closing question built from the next-question chips. It adds no fact
and changes no figure — every sentence out is a sentence in — so the gates
that run after it see the same claims. The model rewrite receives a `STYLE`
line asking for the same shape, so both paths read alike. The "limits vary by
plan tier" nudge is not appended to an introduction.

**2. Conversation memory** — `apps/api/api/memory.py`. One JSON file per
session under `state_dir/sessions/`; a one-line summary per turn — product,
intent, what was asked, the answer's first sentence — and a rolling summary
of the last five. A client that sends `history` is believed; one that sends
nothing gets the session's own earlier questions, so "what does it not
cover" after "tiq home" is about Tiq Home. The summary rides back on the
envelope (`summary`) and `GET /v1/sessions/{id}` returns the record. Where a
model is configured, a background thread replaces the deterministic line
with a one-sentence model summary after the turn has been answered; the
turn never waits on it. `MEMORY=auto` is on in the API server and off in
tests and batch evaluation, which construct settings directly and must stay
stateless.

**3. Next-question suggestions** — `apps/api/api/suggest.py`. Up to four
chips per answer, phrased in the product's own name so the next turn resolves
without a model, chosen by what was just asked (after exclusions, not
exclusions again) and by what the corpus holds for the product (no "how do
I claim" where there is no claims page; no "is there a promotion" where there
is none). A clarifying answer keeps its own chips. The introduction's closing
question is built from the same list, so the words and the taps agree.

**4. Guardrails, both ways.** In: NRIC/FIN, payment-card numbers, emails and
passport numbers are redacted from the turn before the Ask, the model, the
trace or the memory sees it, and flagged (`pii`) — never a refusal. Abuse in
the turn is flagged by the rules layer as well as the model. Out: personal
data in the answer is `leakage` (rules now have standing to block it), a link
anywhere but etiqa.com.sg or tiq.com.sg is `external-link`, abuse is
`toxicity`; all three block on the rules alone and the model may raise them
too. The model-layer screens (`GUARDRAILS=model`) run on top as before.

## Open — needs a person

- **20 conflict tickets** in `okf-real/conflicts/`: places the website
  disagrees with itself. The bot follows the product page; the tickets are
  for the website team.
- **LLM WIKI pages the cross-check left as drafts**: read the ones whose
  sentences the judge did not all entail, or drop them.
- **GPU deployment**: only if a Linux machine with a graphics card exists;
  see the plain-terms note above.
