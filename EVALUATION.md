### Measured, today

Three columns: the v2.3 build that produced this dataset's first score, the
same suite after the routing change (v2.3.1) that its findings prompted, and
after v2.4 — retrieval scoped to the resolved product, the three-layer router,
and asking when the product is unsure (`DESIGN-v2.4.md`).

```
Conversation golden dataset          v2.3         v2.3.1 (routing)     v2.4 (scoped)  

  overall                       1209/1711  70.7%   1333/1711  77.9%   1341/1711  78.4%
    whole conversations          132/355   37.2%    198/355   55.8%    199/355   56.1%
    turns                        994/1373  72.4%   1180/1373  85.9%   1181/1373  86.0%
    context-dependent turns      638/810   78.8%    705/810   87.0%    705/810   87.0%

turns by kind
    switch                         1/4     25.0%      1/4     25.0%      1/4     25.0%
    attack                         1/2     50.0%      1/2     50.0%      1/2     50.0%
    pivot                         18/165   10.9%    124/165   75.2%    124/165   75.2%
    opener                       270/355   76.1%    275/355   77.5%    276/355   77.7%
    repeat                         7/9     77.8%      7/9     77.8%      7/9     77.8%
    ellipsis                     395/448   88.2%    396/448   88.4%    396/448   88.4%
    drill                        219/304   72.0%    291/304   95.7%    291/304   95.7%
    advice                        39/40    97.5%     39/40    97.5%     39/40    97.5%
    escalate                       1/3     33.3%      3/3    100.0%      3/3    100.0%
    correction / recover / closer / pick             100%               100%               100%

by contract
    entity_fact                    2/4     50.0%      2/4     50.0%      2/4     50.0%
    handoff                       43/179   24.0%     96/179   53.6%     98/179   54.7%
    conversation                 132/355   37.2%    198/355   55.8%    199/355   56.1%
    out_of_scope                   3/12    25.0%      8/12    66.7%      8/12    66.7%
    advice_boundary               11/15    73.3%     11/15    73.3%     11/15    73.3%
    clarify                       11/15    73.3%     11/15    73.3%     11/15    73.3%
    product_fact                 974/1092  89.2%    974/1092  89.2%    974/1092  89.2%
    corpus_fact                   19/20    95.0%     19/20    95.0%     19/20    95.0%
    directory                     14/19    73.7%     14/19    73.7%     19/19   100.0%

by journey
    support                       18/57    31.6%     32/57    56.1%     32/57    56.1%
    quote                         36/125   28.8%     71/125   56.8%     71/125   56.8%
    renew                         34/60    56.7%     35/60    58.3%     35/60    58.3%
    cancel                        62/116   53.4%     68/116   58.6%     69/116   59.5%
    service                        7/26    26.9%     18/26    69.2%     19/26    73.1%
    pay                            3/23    13.0%     18/23    78.3%     18/23    78.3%
    claim                        249/357   69.7%    286/357   80.1%    286/357   80.1%
    policy                       405/492   82.3%    406/492   82.5%    406/492   82.5%
    apply                         43/54    79.6%     46/54    85.2%     46/54    85.2%
    eligibility                  199/231   86.1%    200/231   86.6%    200/231   86.6%
    evaluate                      52/58    89.7%     52/58    89.7%     52/58    89.7%
    discover                     101/112   90.2%    101/112   90.2%    107/112   95.5%

owed a handoff and did not give one          145                 87                 85
  ANSWERED — a substantive reply it could not support
                                              82                 51                 50
  asked which product instead of handing off  62                 35                 33
  blocked by a gate rather than handed off     1                  1                  2
```

v2.3.1 against v2.3, case by case: **124 gained, 0 lost.** An earlier cut of this branch lost three
cancel/renew turns on Cancer Insurance, and the first explanation offered for
them — order-dependence in the batch — was wrong. Every "isolation" replay
behind that claim had silently run against the seed bundle, which has no
Cancer Insurance product, so base and branch agreed because neither could
find the page. Replayed against the real bundle, base answered and the branch
did not. The cause was `faq_pick` applying a *question* classifier to FAQ
*headings*: the new `payment` pattern read "When will GIRO deductions be made
for the renewal premium?" as out-of-corpus, the count of renewal headings fell
from two to one, a low-overlap FAQ entry led the answer, and the entitlement
gate rightly blocked it. Headings are now read as topics (`classify_topic`),
which agrees with the pre-split classifier on all 467 FAQ headings in the
corpus, and the three return base's answers. The suite itself is deterministic:
run forwards and reversed it produces byte-identical answers on all 1,711
cases.

**v2.4 against v2.3.1: 8 gained, 0 lost.** Every gain is one of two things.
Five are directory turns ("what insurance products do you offer?", "I need
life insurance") that were previously answered with a two-product coin toss
dressed as a clarifying question and now get the catalogue's lines or the
line's products. Three are turns that named no product and asked something
only a product page could settle ("has my cancellation gone through?"): the
router deferred the product to the corpus, the corpus settled on one, retrieval
was scoped to it, it held nothing on the point, and the turn was handed off
instead of answered from whichever page sorted first. Nothing lost means the
scoping — 1,346 of the 1,711 cases now retrieve from one product's pages and
documents only — cost no answer the wider search had been getting right. The
class of turn that is asked about rather than guessed grew where the plan said
it would (a *question* about a bare category, "does travel insurance cover
skiing?") and not where it did not: the situation openers ("the airline lost
my suitcase") resolve to one product on the corpus alone, and the rule is to
ask only when the corpus does not settle it. What did not move is the product
fact rate, 974/1092, which is now bounded by content — the 31 products without
benefit tables, the wordings that were never supplied — and not by retrieval
picking the wrong product. The seed gate is unchanged at 97/130 with a failing
set byte-identical to the base branch's.

### Reading the numbers

**77.9% is the least useful number here.** The breakdown is the measurement.

**The pivot was the finding, and routing was the answer.** A pivot is the turn
where the customer crosses from what the corpus knows to what only a system
knows: *"and how much is it?"*, *"where is my claim now?"*, *"when will the
refund reach me?"*. At v2.3 the bot got **18 of 165**, and every failure was
one mode — it answered instead of handing off:

| the customer said | after | v2.3 answered with |
|---|---|---|
| *"where is my claim now?"* | four turns about how to claim | the claim-notification clause |
| *"when will the refund reach me?"* | cancelling a policy | the terms of a 2024 promotion |
| *"how much is it?"* | four turns about travel cover | a clause about cover beyond age 70 |
| *"I got an email asking me to confirm my policy details"* | — | *"Please log in to TiqConnect to update your details"* |

Every gate passed that last one: the sentence is a faithful quotation of a
real, approved, dated page, and it is also what a phishing sender wants a
customer to do. **Groundedness is not aboutness.** v2.3.1 classifies those
turns instead of leaving them `unknown` — which `gate_answerability`
documents as *unconstrained* and passes on purpose — refuses them before
retrieval, and names the page that does know. `pivot` **10.9% → 75.2%**.

**Conversation made it worse, not better** — which is why a single-turn suite
could not find it. Asked cold, *"how much is it?"* names no product and the
bot has little to say. Asked on turn three it has a product in hand and
confidently keeps answering from its pages. Having context turned a shrug into
a wrong answer.

**The pipeline follows a subject well, and always did.** `ellipsis` 88.2% and
context-dependent turns 86.9% say the history it carries genuinely works.
`recover` 3/3 says an attack midway does not poison the turn after. What failed
was never memory — it was noticing the customer had changed gear.

**Product knowledge is the strong half and stayed there.** `product_fact` 89.2%
→ 89.2% across all 37 products: routing did not eat the questions the corpus is
for, which was the risk worth measuring. The formerly weak journeys were the
transactional ones — `pay` 13.0% → 78.3%, `service` 26.9% → 69.2%, `support`
31.6% → 56.1% — where the corpus holds nothing and the honest reply is a
destination.

**What is left is the other failure.** 87 turns still owe a handoff, and 28 of
them are one deliberate omission: a bare *"how much will I get back?"* is read
as a limit. After a cancelled *policy* it is a premium refund; after a
cancelled *trip* it is the trip-cancellation benefit, a figure in the benefit
table. An early cut read the second as the first and sent three real figure
questions to the customer portal, so only the forms that name a refund route.
Separately, `cancel` at 58.6% and `quote` at 56.8% are the opposite failure —
the bot refusing what the corpus does hold — which routing must not paper over.

**A failing case is not always a bot defect.** The first cut of this dataset
asserted `advice_flag: false` on every product fact and scored Tiq Invest — the
one product whose frontmatter carries `regulated_advice` — at 1/27. The bot was
right and the dataset was wrong; the generator now reads that flag and asserts
the opposite for advised products. Check the contract before filing a bug.

The dataset guards itself: `evals/test_conversation_dataset.py` asserts the
committed suite still matches the taxonomy, that ids are unique, that every
case carries its labels, that a handoff case never asserts a citation, and that
the date stays pinned.

---

## 5. Guardrail backtest

```bash
make guardrail-backtest
```

```
labelled corpus      199 turns (92 benign / 107 hostile)
  rule layer         precision 1.0000  recall 1.0000  F1 1.0000
                     tp 79  fp 0  tn 120  fn 0

held-out benign traffic (never used to tune a pattern)
  generated eval suites                   25791 questions  0 raised  (0.0000%)
  questions published on the websites      1927 questions  0 raised  (0.0000%)
```

Three things, and the third is the one that matters:

1. **Accuracy** on `apps/api/tests/guardrail-scenarios.yaml` — 199 labelled turns.
2. **Generalisation** against 27,718 benign questions the patterns were never
   tuned on. **The target fails on a single held-out false positive.** A
   guardrail that blocks real customers is worse than one that misses an attack,
   because you never find out about the customers.
3. **Threshold headroom** — how close each category sits to acting on a lone
   model verdict, so you can see which one will tip first.

The rule layer always runs and is not switchable. A model layer may only ever
**raise** risk, never lower it, and the two are combined per category with a
noisy-OR — monotone, which is what preserves that property.

---

## 6. Bundle lint

```bash
make lint-bundle                                   # the seed bundle
uv run python -m compiler.cli --bundle okf-real lint
curl -s http://localhost:8080/v1/bundle/lint       # against a running container
```

```
$ make lint-bundle
23 pages · 25 table rows · 0 errors · 0 warnings

$ uv run python -m compiler.cli --bundle okf-real lint
0 errors · 0 warnings          # 768 pages, 73 table rows
```

Corpus integrity rather than bot behaviour: unreferenced claims, numbers typed
into prose, broken graph edges, transclusions with no row, routes baked into
product pages. Rules are in [CORPUS.md](CORPUS.md#stage-5--lint).

---

## Known findings — the guard that matters more than the gate

`apps/evalgen/tests/known-findings.json` records every open defect the generated
suite reports against the seed bundle, **case by case**:

| finding | cases |
|---|---|
| `advice-boundary-misses-recommendation-phrasings` | 6 |
| `situational-phrasing-does-not-reach-the-benefit` | 7 |
| `unanswerable-question-answered-with-off-topic-prose` | 15 |
| `underwriter-named-obliquely-not-verbatim` | 2 |

`test_pipeline_e2e` asserts **both directions**:

- nothing may fail that is not on this list — a new failure is a regression;
- nothing on the list may start passing without the list shrinking to say so.

That second direction is the point. A pass rate can drift upward for reasons
nobody understands; this file makes every open defect a named thing with an
owner, and closing one is an edit to this file, not a number moving.

The seed gate is 0.91 rather than 0.95 **because** of these four findings. The
gate is the backstop; the file is the guard.

---

## Choosing a model

| `LLM_PROVIDER` | needs | cost |
|---|---|---|
| `deterministic` *(default in CI)* | nothing | free, offline, ~4 ms |
| `anthropic` | `ANTHROPIC_API_KEY` | 3 calls/case |
| `vllm` | `VLLM_BASE_URL`, `VLLM_MODEL` | local compute, ~3.3 s/case |

Measured on the same 604-case suite and the same 100 conversations:

| | deterministic | Qwen3.6-35B-A3B (4-bit, local) |
|---|---|---|
| accuracy | 95.0% | 94.2% |
| citation F1 | 0.958 | 0.958 |
| figure exact match | 96.9% | 96.9% |
| numeric binding | 100.0%, 0 unbound | 100.0%, 0 unbound |
| conversations (whole / turns) | 96.0% / 98.8% | 96.0% / 98.8% |
| latency p50 / p95 | 3.9 / 4.3 ms | 3,342 / 5,331 ms |

Everything except accuracy and latency is identical, and that is structural, not
luck: none of those rows is the model's to decide. Retrieval picks the pages,
the transclusion pass resolves every figure against a row or a verified
quotation, and the model is handed prose to write.

The 0.8-point gap is five `gap-*` cases — questions the corpus cannot answer,
where the deterministic composer refuses flatly and the model writes something
fluent around the hole without setting `handoff`. Nothing unbound or leaked got
through. A non-answer delivered as an answer is the softer version of the same
failure, and it is the one a model introduces.

Running the real-corpus suite against a local model is not practical: 25,791
cases at ~12 cases/min is roughly 36 hours.

---

## Where the output goes

```
.eval-reports/
  auto-eval.{json,md,html}      the last auto-eval run
  auto-suite.json               the generated suite — every question asked
  web/, qwen/                   whichever --out you passed
  guardrail-backtest.json
```

Gitignored. `.json` for trending, `.md` for pasting into a review, `.html` to
read. Pass `--out` to keep runs apart — without it the next run overwrites the
last, which is how one 2-hour real-corpus report got clobbered by a 3-minute
seed run.

---

## Adding coverage

**A behaviour that must never regress** → a case in `evals/suites/*.yaml`. Gate
is 100%; keep it deliberately small.

**A behaviour that should hold across the corpus** → a surface form in
`apps/evalgen/evalgen/surfaces.py`. It will be generated for every product that
has the underlying fact, and the per-product floor will hold you to it.

**A conversational failure** → an archetype in
`apps/evalgen/evalgen/conversations.py`.

**A guardrail case** → a labelled turn in
`apps/api/tests/guardrail-scenarios.yaml`. Add benign turns as readily as
hostile ones; the false-positive rate is the metric that matters.

**A defect you cannot fix now** → an entry in `known-findings.json` with a note
saying why. That is the honest alternative to lowering a gate.

## v2.1 — measured (2026-09-03)

Same 1,000-case random sample of the generated suite (seed 20260827), live on
Qwen (`qwen3.6-35b-a3b`, MLX), scored with the corrected metric — a delivered
answer citing the wrong page is unsafe. Build `4177480` (the two later
clarification guards in `c4bf71b` are not in this run).

| | v2 (re-counted) | v2.1 |
|---|---|---|
| accuracy | 84.5% | 82.6% |
| **unsafe** — a wrong answer delivered | **114** | **71** |
| **wrong product** delivered | **11** | **2** |
| safe misses — declined | 41 | 103 |
| citation F1 | 0.771 | 0.725 |
| numeric binding | 100% | 100% |
| entitlement leaks | 0 | 0 |
| entailment judge engaged | — | 815 / 1000 (185 fell back to overlap under load) |

This is the trade the plan set out to make, and it is the number that
decides: unsafe fell by 38% and wrong-product by 82%, by becoming safe misses
rather than by editing the suite. Accuracy gave up 1.9 points for it. The
misses are the next target — recall is what the pgvector layer is for, and it
was off for this run.

Field test (104 customer-phrased turns), live on Qwen: v2 78/104 (75.0%) →
v2.1 80/104 (76.9%).

Run at three workers so the judge stays inside its 30 s timeout; 185 turns
still fell back, and the tally says so rather than counting them as judged.

