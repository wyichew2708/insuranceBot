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

## Open — needs a person

- **20 conflict tickets** in `okf-real/conflicts/`, including the
  travel-delay threshold (product page 3 hours, schedule 6). LLM WIKI drafts
  written from a contested source repeat the contest; they land as `draft`
  and stay unretrievable until reviewed.
- **Which slug survives a fold** is chosen for plainness (no brand prefix,
  then shorter), which kept `tiqinvest` over `tiq-invest`. Harmless — both
  names are aliases — but if the readable slug is wanted, that is a one-line
  preference.
- **LLM WIKI review**: `make llm-wiki` output, all `draft`, one product at a
  time.
- **GPU deployment** (plan step 7): vLLM, pgvector, TEI on the Linux box —
  the configuration exists (`infra/docker-compose.yml`, `gpu` profile); the
  measurement has to be taken there.
