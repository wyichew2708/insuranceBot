# Evaluating the bot

Six suites, what each one is actually testing, how to run it, and how to read a
failure. Every number here was measured on this repository — the run log is
quoted, not summarised from memory.

**Everything is offline and free by default.** `LLM_PROVIDER=deterministic`
means no network, no key, no cost. A configured model makes up to three calls
per case — screen the question, write the answer, screen the answer — so a
25,791-case suite becomes ~77,000 billed requests. The Makefile targets pin the
provider for exactly that reason; the `-live` variants are the ones that spend
money.

---

## The one command

```bash
make ci
```

Lint, typecheck, 620 tests, bundle lint, guardrail backtest, curated suites,
seed auto-eval, fixture compile, fixture auto-eval. Exits non-zero if any gate
fails. This is what has to be green before a push.

---

## 1. Unit tests

```bash
make test                                 # or: uv run pytest
uv run pytest apps/compiler -q            # one package
uv run pytest -k exclusion -q             # one topic
```

```
620 passed, 2 skipped
```

The two skips are network-dependent. A `conftest.py` autouse fixture forces
`LLM_PROVIDER=deterministic` for the whole suite, so a populated `.env` cannot
silently turn `pytest` into a billed run — a mistake that once cost ~1,830 API
calls per invocation.

---

## 2. Curated suites — the regression gate

```bash
make evals                                # deterministic, gate 100%
make evals-live                           # against whatever .env configures
uv run python -m evals.runner --suite golden --gate 1.0
```

Four YAML suites in `evals/suites/`, 21 cases, hand-written:

| suite | what it pins |
|---|---|
| `golden` | the answers that must never change |
| `merge-consistency` | one product, several routes to market, identical facts |
| `adversarial` | injection, entitlement probing, advice-seeking |
| `staleness` | live promotions quotable, expired ones not, overdue pages demoted |

```
overall 21/21 (100.0%), gate 100%
```

The gate is **100%** and should stay there. These are not sampled behaviours;
they are the contract. A failure here is a regression, full stop.

`evals-live` exits 2 if any case was silently served by the deterministic
fallback — a case served by the fallback measured the fallback, not the model.
`--allow-fallback` overrides that when you mean it.

---

## 3. Auto-evaluation — the suite that grows with the corpus

```bash
make autoeval                             # seed bundle, gate 0.91
make autoeval-web                         # fixture bundle, gate 0.94
make autoeval-live                        # seed bundle, real model, costs money

# any bundle, any gate:
LLM_PROVIDER=deterministic GUARDRAILS=rules \
uv run python -m evalgen.cli --bundle okf-real --out .eval-reports/real \
  --gate 0.95 --min-per-product 100 all
```

Subcommands: `generate` (derive the suite), `run` (execute and score),
`report` (render), `all` (the lot). The suite is written to disk before it runs,
so you can read exactly which questions were asked and what evidence each
expected.

Nothing is hand-written. Every case is derived from something already in the
bundle — a benefit-table row becomes "what is the X limit?" pinned to that row
id and tier; an authored alias becomes a question; an exclusions section becomes
"are X covered?"; a product line the corpus does *not* carry becomes a case that
must hand off rather than answer from the nearest neighbour.

Two consequences. The suite **grows with the corpus** — publish fifty product
pages and their questions appear without anyone writing YAML. And coverage is
**measurable**: any page no question reaches is named in the report.

`--min-per-product 100` fails the run if any product has fewer than 100 cases
attributable to it, so a product the corpus barely describes cannot look strong
by being asked less.

### Measured, today

| bundle | cases | accuracy | gate | |
|---|---|---|---|---|
| `okf` seed | 604 | **95.0%** | 0.91 | pass |
| `okf-web` fixture | 4,193 | **94.7%** | 0.94 | pass |
| `okf-real` | 25,791 | **82.3%** | — | no gate set |

Real corpus detail:

```
  accuracy              82.3%   (25791 cases)
  citation F1           0.837
  figure exact match    79.5%
  numeric binding      100.0%   (0 unbound)
  safety                61.2%   (0 leaks)
  failure shape       1791 unsafe / 2772 safe misses
  recall@1 / @3 / MRR 0.79 / 0.97 / 0.91
  latency p50/p95     118.49 / 516.52 ms
  corpus reach          97.5%   (rows 88%)
```

The real corpus has no gate because it is not a regression suite — it is a
measurement of a corpus that is still being built. It takes **~2 hours**, so
run it in batches:

```bash
uv run python scripts/eval_batches.py --bundle okf-real \
  --suite .eval-reports/real-suite.json --batch-size 1000
uv run python scripts/eval_batches.py --bundle okf-real --report-only
```

Each batch is written to `.eval-reports/batches/batch-NNNN.json` the moment it
finishes and prints its own pass rate, a running total and an ETA. A rerun
skips what is already on disk, so a kill costs one batch rather than two
hours — which had already happened three times before this existed. The score
is computed from the files rather than from memory, so `--report-only`
reproduces it after a crash, on another machine, or a week later.

Deterministic and pinned to it: a configured model would make this three API
calls per case, about 78,000 for one pass, which is not a thing to start by
accident. `--live` opts in.

### Reading the numbers

**`numeric binding` is the one that must never move.** 100.0% with 0 unbound
means no number reached an answer without a benefit-table row or a verified
quotation behind it. If this drops below 100, stop and find out why before
looking at anything else.

**`failure shape` splits failures by what they cost you.** A *safe miss* is a
refusal or handoff where an answer existed — annoying. An *unsafe* failure is an
answer that was wrong, uncited, or cited the wrong page — dangerous. 1,791 of
25,791 is a 6.9% unsafe rate, against 21.1% on the previous real-corpus run.

**`corpus reach`** names pages no question touched and rows no answer exercised.
Low reach means the generator cannot see part of your corpus, which usually
means those pages lack aliases.

**`accuracy` alone is not comparable across bundles.** Different corpora
generate different case mixes. 82.3% on the real corpus against 95.0% on the
seed is not "the real corpus is worse at answering" — it is that 4,563 of its
failures are `fig-*` cases, because 10 of its 108 products have a benefit table.
The text tier is compiled; the figure tier is thin.

---

## 4. Multi-turn conversations

There is no Makefile target; it runs from the test suite or a three-line script:

```python
from evalgen.conversations import generate
from evalgen.conversation_runner import run_suite, summarise
from okf import Bundle
from api.settings import Settings

root = Path("okf-real")
bundle = Bundle.load(root)
report = run_suite(bundle, Settings(bundle_path=root), generate(bundle, str(root)))
print(summarise(report))
```

100 conversations, 325 turns, eight archetypes: a customer exploring, one
correcting themselves, one switching topics, an attacker mid-thread, someone
asking for advice at the end. Deterministic — the same bundle yields the same
suite, so two runs are comparable.

| | seed (3 products) | fixture (22) | real (108+) |
|---|---|---|---|
| whole conversations | 96.0% | 56.0% | 46.0% |
| turns overall | 98.8% | 74.8% | 67.4% |
| standalone turns | 100.0% | 85.4% | 79.4% |
| context-dependent turns | 96.8% | 57.9% | 48.4% |
| self-contradictions | 0 | 0 | 0 |
| attacks held | 24/24 | 24/24 | 24/24 |
| answered the next turn | 12/12 | 12/12 | 10/12 |

**Whole-conversation rate is the honest number.** Customers do not experience
turns, they experience conversations, so one bad turn spoils the exchange and
the conversation rate is never above the turn rate.

**Nothing the customer said earlier is carried.** The session holds channel,
auth level and policy; the question text does not accumulate. That is what
`context-dependent` measures, and the three columns show the shape of it: an
elliptical follow-up — "and the premier tier?" — is retrieved on its own words,
which lands on the right product when there are three and lands anywhere when
there are a hundred. The seed bundle's 96.8% is not the system resolving
reference; it is a corpus small enough that failing to resolve it does not
matter.

Two rows hold everywhere, and they are the ones the gates own: **no conversation
ever gave two different figures for one fact**, and every attack was refused
mid-thread without the bot then punishing the customer by staying refusing.

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
