# Accuracy enhancement and evaluation plan

## Objective and implementation base

Improve the relevance, completeness and applicability of insurance answers,
while preserving correct refusals, clarification, provenance and audience
boundaries. Measure the finished system against the same v2.8 corpus and
golden cases before claiming an improvement.

Base commit: `b1cf2e3` on `claude/genai-insurance-chatbot-v2.7`.
Despite the branch name, this commit contains the v2.8 reports and rewrite.
The earlier architectural review of `main` described v1; its findings must
be rechecked against v2.8 rather than assumed to remain defects.

This commit publishes the plan only. Draft implementation and additional
golden cases exist in the working session but are not part of this plan
commit, and have not passed full validation.

## Recorded baseline

| Suite | Corpus | Passed | Rate |
|---|---|---:|---:|
| Conversation cases | okf-real | 1680/1711 | 98.2% |
| Whole conversations, every scored turn correct | okf-real | 336/355 | 94.6% |
| Handwritten field test | okf-real | 98/109 | 89.9% |
| Existing CI seed gate | okf | 105/130 | 80.8% |

Sources: `evals/reports/v2.8-conversation.md`,
`evals/reports/v2.8-field-test-okf-real.txt`, and
`evals/reports/v2.8-seed-gate.txt`.

These measure the deterministic composer, not live-model accuracy. The seed
gate has a 100% threshold and currently fails. Some field-test expectations
reference products absent from the seed corpus; preserve visibility of this
mismatch rather than describing all failures as answering defects.

## Phase 1 — Establish reproducible measurement

- [x] Identify the authoritative v2.8 reports and base commit.
- [x] Prepare an isolated baseline checkout at `b1cf2e3`.
- [ ] Install the locked runtime and development dependencies.
- [ ] Run baseline conversation, field-test, seed and existing unit suites.
- [ ] Record commit, corpus and suite hashes, Python/dependency versions,
  provider, settings and evaluation dates alongside each report.
- [ ] Save complete per-case JSON for baseline and candidate, including failed
  turns, citations, selected product, routing, gate verdicts and timings.

Completion: the baseline is reproducible with the same input files and
settings. Explain any difference from the committed snapshots before tuning.

## Phase 2 — Context, intent and clarification

- [ ] Complete and validate the draft price-question recognition changes.
  Distinguish premium cost from claim amount, excess, refund and benefit limit.
- [ ] Treat explicit topic switches as boundaries; do not revive the previous
  product when the new category is unresolved.
- [ ] Recover a previously confirmed product on a return to that topic only
  when the history identifies it unambiguously.
- [ ] Interpret claim-rejection follow-ups such as “why?” and servicing
  follow-ups such as “and my phone number” in the immediate conversation.
- [ ] Ask targeted questions for personal medical conditions and unclear
  referents; never infer eligibility or cover from the condition alone.
- [ ] Prioritise the affected person's role in incident matching, including
  domestic helpers and pets, while preserving explicit product-name priority.
- [ ] Clarify standalone acknowledgements without breaking legitimate
  confirmations, numbered selections or explicit corrections.

Completion: paired paraphrases and counterexamples pass; prior passing cases
are retained or any genuine contract conflict is explicitly reviewed.

## Phase 3 — Insurance evidence and answer relevance

- [ ] Trace every remaining neighbouring-section failure in the field test.
  Check that the answer addresses the question, not merely that it is cited.
- [ ] Audit all retrieval paths for consistent audience, brand, product,
  language and applicable-version filtering, including page/graph reads.
- [ ] Verify independent lexical and dense candidate retrieval in the v2.8
  implementation; benchmark fusion and reranking where configured.
- [ ] Expand benefit evidence through relevant exclusions, definitions,
  conditions and endorsements using existing OKF relationships.
- [ ] Preserve table headings, plan columns, units, footnotes and source
  locators through extraction, compilation and answer construction.
- [ ] Make source precedence explicit and owner-approved; detect conflicts
  and escalate when the applicable wording cannot be established.
- [ ] Bind material claims and figures to supporting passages and verify
  applicability and omitted qualifications, not citation presence alone.
- [ ] Allow bounded retrieval repair when evidence is missing, then verify
  the revised answer or fallback. Do not repeatedly rewrite inadequate evidence.
- [ ] Check guarantees and advice boundaries on the wording itself; routing
  markers must not make unsupported promises acceptable.

Completion: difficult coverage/exclusion cases pass claim-support, relevance
and completeness checks, with auditable evidence and bounded execution.

## Phase 4 — Language support and user experience

- [ ] Introduce language-aware question interpretation for Malay, Chinese and
  mixed-language enquiries, preserving product names, negation and amounts.
- [ ] Separate deterministic supported vocabulary from configured model-based
  interpretation; disclose fallback and avoid claiming complete language support.
- [ ] Add human-reviewed language cases, including Malay car-premium questions,
  emergency descriptions, exclusions, ambiguous terms and code switching.
- [ ] Verify customer-visible clarification, handover, citations and action
  rendering for these routes.

Completion: report language results separately and retain unknown-language
and uncertain-meaning clarification paths.

## Phase 5 — Golden dataset and evaluation integrity

- [ ] Validate the draft 29 parser contracts and 12 end-to-end contracts.
- [ ] Add expert-authored paraphrases, minimal contrast pairs, multi-turn
  corrections, ambiguous scenarios, missing evidence and conflicting versions.
- [ ] Include positive assertions for the required action/product/evidence;
  absence-only checks must not reward empty answers or universal refusal.
- [ ] Score every meaningful conversation turn, not only the final answer.
- [ ] Keep development regressions separate from an independently reviewed
  held-out set; do not call cases used for tuning an unseen benchmark.
- [ ] Keep the original comparison datasets frozen during implementation.
- [ ] Add a real-corpus CI evaluation step without silently removing existing
  seed cases or lowering any threshold. Resolve corpus mismatch explicitly.
- [ ] Strengthen comparison tooling to detect added, missing or duplicate case
  IDs and changed expectations, rather than treating them as ordinary gains.

Completion: every reported improvement uses matching cases/corpus/settings,
and new coverage is reported separately from the original baseline.

## Phase 6 — Performance and operational correctness

- [ ] Measure latency p50/p95, tool/model calls, retries and fallback rates by
  route and provider; distinguish deterministic and live-model results.
- [ ] Retain fast paths for approved facts and procedures; parallelise
  independent retrieval only where it preserves evidence and isolation.
- [ ] Key any cache by access scope, brand, product/version, language and
  bundle version, with explicit invalidation on publication.
- [ ] Audit candidate-bundle evaluation, atomic publication and rollback across
  knowledge, catalogue and actions in the rewritten architecture.
- [ ] Record judge outages and model fallbacks honestly; do not count a
  fallback response as a successful live-model measurement.

Completion: correctness gains do not conceal latency regressions, stale
evidence, publication inconsistencies or unavailable verification.

## Validation and comparison procedure

Run existing suites in both isolated checkouts. Preserve baseline reports
before any candidate run, and use identical dates and provider settings.

```bash
uv sync --frozen
make lint typecheck test
LLM_PROVIDER=deterministic GUARDRAILS=rules make conversation-eval
LLM_PROVIDER=deterministic GUARDRAILS=rules uv run python -m evals.runner \
  --suite field-test --bundle okf-real --gate 0
make evals
```

The field-test `--gate 0` command is diagnostic, not a replacement for a merge
gate. Capture the existing failing seed gate rather than hiding its exit code.
On the candidate, also run the new accuracy suite once its files are committed.

```bash
uv run python scripts/diff_runs.py /path/to/baseline/conversation.json \
  /path/to/candidate/conversation.json --show-lost
```

Final report must include:

- Original-suite pass counts and rates before/after; cases gained and lost.
- Whole-conversation and context-dependent-turn outcomes.
- Breakdowns by contract, journey, product, language and safety category,
  with sample counts so small categories are not overinterpreted.
- Incorrect substantive answers, unnecessary refusals, missed handovers and
  clarification quality alongside answer rate.
- New-suite results separately, plus latency and provider/fallback metadata.
- Remaining failures and release blockers; no unmeasured accuracy claims.

## Current blocker and handoff

Full dependency installation has not succeeded in this Work execution
environment. Both installation and a simple PyPI connectivity probe returned
“network approval was cancelled before a decision was returned,” including
after the user enabled Work network access. This records the observed tool
result, not a conclusion that the user declined the request.

The local draft's 29 parser cases pass, and a parser-only comparison of 2,893
existing turns changed seven intent/product/family readings. Neither is a
full answer evaluation. The remaining full plan and baseline/candidate
comparison must run in an environment with the locked dependencies available.

Do not merge the unvalidated draft or report a new overall accuracy percentage
until the full evaluations and comparison above are complete.
