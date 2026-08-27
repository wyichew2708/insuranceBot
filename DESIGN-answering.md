# Redesigning the answering loop

A proposal. Step 1 of §7 is now built; everything else is unbuilt.

The question is how to make answering accurate enough to put in front of
customers. The short version: **the verification half of this system is good
and the selection half is a pile of constants**, and no amount of further
tuning fixes the second because tuning is the problem.

---

## 1. What is actually failing

Eight wrong answers were reported from the chat surface over one working
session. Every one was traced to root cause:

| what was asked | what came back | root cause |
|---|---|---|
| what life products | Products Liability | matched the word "products" |
| looking for ci product | an investment-linked plan | tokeniser drops 2-letter words; `ci` never reached scoring |
| cancer insurance | the pet-insurance FAQ | scores tied, tiebreak was alphabetical |
| want to buy cancer insurance | the home-insurance FAQ | IDF scores `want` 0.791, `cancer` 0.408 |
| term life | the suicide clause | exclusions page outscored the product page |
| what's the coverages | refused | no conversation memory |
| hi | "passing you to a colleague" | no path for a turn that is not a question |
| (a claims answer) | refused at the gate | linter and gate disagreed on what a number is |

Two of those are missing features. **Six are ranking accidents.**

Meanwhile, across 25,791 generated cases and 325 conversation turns, measured:

```
numeric binding      100.0%   0 unbound figures
entitlement leaks    0
self-contradictions  0        (three bundles, 325 turns each)
attacks held         24/24
```

Nothing invented a number. Nothing leaked. Nothing contradicted itself. **The
gates are not the problem and must not be touched.** The problem is upstream:
deciding what the question is about, and which evidence answers it.

---

## 2. Why tuning cannot fix it

Selection is an additive score over a bag of words. There are **17 hand-tuned
constants** in the two files that do it — `+0.6` for a definition question
hitting a concept page, `-0.8` for a coverage question hitting an exclusions
page, `+0.35 + 0.9 × overlap` for a heading match, `-0.4` for a child page
when the turn is a bare product name. Twelve of those are in `select_sections`
alone, and six were added in one day, each to fix one reported failure.

They interact. Removing speech-act words to fix "want to buy cancer insurance"
broke "how do I buy travel insurance", because `buy` is noise for choosing a
*product* and signal for choosing a *section*. That was caught by four tests.
The next collision may not be.

This is the shape of a system that has no model of the question. It has a
proxy — word overlap, weighted by corpus rarity — and every failure adds a
correction term to the proxy. IDF ranking `want` above `cancer` is not a bug in
the weights. It is IDF answering the question it was asked: *how rare is this
word in a corpus of contracts?* A conversational verb is very rare in
contracts. That is a true answer and a useless one, and no coefficient fixes a
signal that means the wrong thing.

---

## 3. The structural gap

The pipeline today:

```
guardrail-in → reference → expand → filter → read → rag? → sor
             → compose → generate → guardrail-out → gates → deliver | refuse
```

It has a **classifier** (`classify()`, lexical) and a **verifier** (the eight
gates). It has no **planner** and no **loop**.

Two specific consequences, both verifiable in the code as it stands:

**`REQUIREMENTS` is used only to refuse.** `harness/intent.py` already encodes
what evidence would settle each intent — an exclusion question needs a page
whose id ends `/exclusions`, a limit needs a bound figure, a claim needs a
journey page. It has exactly one consumer: `gate_answerability`, which reads it
*after* the answer is written, to reject. Nothing uses it to go and **fetch**
the exclusions page. Whether the right page loads is left to lexical luck, and
then punished when the luck runs out.

**A failed gate is terminal.** `blocked(results)` → `HANDOFF`. But many
failures name exactly what is missing: `coverage asserted without reading
['product/general/home/exclusions']` is a gate telling us the page id it wanted.
The system has that string and throws the turn away instead of loading it.

---

## 4. The redesign

Five changes. Ordered by value; each independently shippable.

### 4.1 Understand the question with a model, over a closed vocabulary

Replace the lexical guess at *which product* with a structured resolution:

```json
{ "products": ["product/protection/cancer-insurance"],
  "facet": "exclusion",
  "confidence": "high",
  "ambiguous_between": [] }
```

The model picks **from ids that exist in the bundle**. It does not generate a
fact, a figure or a page — it selects, and selection from a closed set is
checkable by existence. "want to buy cancer insurance" is trivially about
cancer insurance for any competent model; it defeated IDF.

This draws the same line the system already draws elsewhere and for the same
reason. The model writes prose and never establishes a fact, which is why
binding is 100%. Here it identifies a subject and never asserts one.

Three safety properties, all mechanical:

- an id that does not resolve is discarded;
- on absence, timeout or malformed output, fall back to today's lexical path —
  so this can improve selection and cannot degrade it;
- the deterministic path stays complete, so CI and the eval suites keep running
  offline and free.

### 4.2 Let requirements drive retrieval, not just rejection

Invert the existing table. An exclusion question fetches the exclusions page
**because the requirement says that is what settles it**, not because word
overlap happened to surface it. A limit question fetches the benefit table for
the resolved product and tier.

This converts "did the right page load?" from luck into construction, and it is
the single cheapest change here — the data already exists and is already
trusted enough to refuse on.

### 4.3 Check sufficiency before composing, not after

Between retrieval and composition, ask: *does this evidence settle the
question?* The requirement already defines the answer.

Where it does not, say precisely what is missing — "I have the exclusions for
this plan but not the limits for your tier; sign in and I can give you those" —
rather than the current generic refusal. A customer told *why* can act; a
customer told "I could not establish that" cannot.

### 4.4 A bounded repair loop

A gate failure that names a missing page is recoverable. Load it, recompose,
re-run the gates. Bounded at two iterations and charged to the existing budget.

The gates stay authoritative on the final answer — this changes what happens on
the way to them, never what they accept. It converts a class of refusals into
correct answers without weakening a single check. `exclusion-completeness`
alone accounted for three of seven refusals in the last 97-turn simulation, and
every one of those named the page it wanted.

This is the part that earns the word *reasoning*: act, check, correct. Not a
model musing in a scratchpad — a loop with a verifier in it.

### 4.5 Ask instead of guessing

When two products are plausible and the question does not separate them, ask:

> Did you mean Cancer Insurance, or the CI rider that attaches to a life policy?

The system cannot do this today. `focus_product` picks one and **excludes every
other product from retrieval** — which is precisely how the cancer question was
answered from the pet FAQ. One coin toss, and the right page was filtered out
as "a different product".

For a customer-facing assistant this may be the highest-value behaviour in the
whole proposal. A clarifying question is never wrong. A confident wrong answer
about someone's insurance is the failure that matters.

---

## 5. What must not change

Invariants. A redesign that breaks any of these is not an improvement:

1. **No number reaches an answer without a benefit-table row or a verified
   quotation.** 100.0% today.
2. **The model never establishes a fact.** It may select from a closed set and
   phrase what was established. Nothing else.
3. **The eight gates remain authoritative and post-hoc.** Reasoning proposes;
   gates dispose.
4. **The deterministic path stays complete.** No model, no network, no key —
   the eval suites and CI depend on it.
5. **Every claim keeps its source.** Compile-time provenance is untouched.
6. **A model layer may raise risk and never lower it** — the guardrail rule,
   extended to selection: reasoning may add a candidate, never suppress a
   verification.

---

## 6. How it gets proven

Not "it feels better". The suites exist:

| suite | today | gate for the redesign |
|---|---|---|
| numeric binding, real corpus | 100.0%, 0 unbound | must stay 100.0% |
| entitlement leaks | 0 | must stay 0 |
| curated suites | 21/21 | must stay 21/21 |
| auto-eval, seed | 95.0% | no regression |
| auto-eval, real corpus (25,791) | 82.3% | the number to move |
| conversations, real (325 turns) | 46.0% whole / 67.4% turns | the other number to move |
| guardrail held-out false positives | 0 / 27,718 | must stay 0 |

Two additions worth building alongside:

- **A selection-accuracy metric.** Every generated case already knows which
  page *should* answer it. Nothing currently reports how often the right
  product was chosen, separately from how often the answer was right — which is
  the number this whole proposal is about.
- **A clarification-rate metric.** Asking is good; asking constantly is a
  different failure.

---

## 7. Staging

| # | change | risk | why this order |
|---|---|---|---|
| 1 | ~~Requirements drive retrieval (4.2)~~ **built** | low | data exists and is already trusted; no model needed |
| 2 | ~~Repair loop (4.4)~~ **not needed** | low | prevention removed the failures it targeted — see below |
| 3 | ~~Sufficiency check (4.3)~~ **built** | low | turns generic refusals into specific ones |
| 4 | Model-based resolution (4.1) | medium | new dependency; falls back to today's path |
| 5 | Clarifying questions (4.5) | medium | needs the conversation surface to carry a pending question |

**Step 1 is built.** `Requirement` gained `holds_answer` — the page suffixes
that carry the answer, read *before* composing rather than after — and the
composer boosts those pages. It was prompted by "how to buy" being answered
from three FAQ entries that repeat the word "buy" while the product's own
"How to buy" section sat unread on a page that was already loaded.

Three things went wrong building it, all caught by the suites, all worth
recording because they are the shape of this kind of change:

* a **penalty** on un-named pages starved the benefits page on every coverage
  question — about a hundred cases. Steering is adding weight to the right
  evidence, never removing it from the rest;
* adding `coverage` and `definition` to the requirement table to carry a
  suffix made the answerability gate demand something of them, and it had been
  deliberate that those two demand nothing. A requirement with no checkable
  clause now cannot refuse;
* steering `coverage` at all was wrong — it is the catch-all that "are wear and
  tear covered?" falls into, and that question wants the exclusions page.

Simulation after: 105 turns, 95 answered, 4 smalltalk, 6 refused.

**Step 2 was not built, and should not be.** The repair loop was designed to
recover `exclusion-completeness` failures by loading the page the gate named
and recomposing. Building it started by making the gates report what they
wanted in a typed field rather than in English — worth having, and kept — and
that immediately showed the failures all had one cause.

Only `product/<line>/<slug>` carries `links`; its `/faq`, `/conditions` and
`/cover` children carry none. A turn that retrieved a child page and not the
parent reached no exclusions page at all, asserted coverage from the child,
and was refused. Traversal now follows the typed edges of the product a page
*belongs to*, so the exclusions load before composition and the answer is
formed in their presence.

That is the honest version of the same idea. A repair loop that loaded the
page and re-gated without recomposing would have made the check vacuous —
the gate exists so coverage is asserted *in the presence of* the exclusions,
not merely alongside them. Prevention gets the property the gate is protecting;
repair would have got the green tick.

Simulation after: 105 turns, **98 answered**, 4 smalltalk, 3 refused — the
injection attempt, an advice question, and one genuine miss.

The loop stays unbuilt until a failure appears that it would actually fix.
Building machinery with no failures left to catch is how a system acquires
parts nobody can justify.

**Step 3 is built.** A refusal now names what is missing where the intent makes
that knowable — no premium is published in this corpus, no document is
deliverable through it, a limit depends on a plan tier an anonymous session
does not know. Only the `answerability` gate earns this treatment: it means
"nothing loaded settles this", which a customer can act on. Every other gate
means "we caught a problem with the draft", and dressing that up as a missing
premium would be false.

Building it found two clauses passing on coincidence, both of which had been
delivering wrong answers rather than refusals:

* "how much does travel insurance cost a year" was answered from a wording
  about lost passports, because `needs_figure` was satisfied by *any* bound
  figure and the section quoted `$350`. A price question is settled by a
  premium, not by a number — `needs_figure_label` now says which;
* "send me the policy wording" passed because the answer contained the words
  "contract" and "premium", which appear in almost every wording.

Answered fell 98 → 96 in the simulation, and that is the improvement: both of
the turns it lost were wrong answers that are now honest refusals.

1–3 need no model and should land first. They are also the ones that make 4
safe, because a resolution the model gets wrong is caught by a sufficiency
check that is already there.

---

## 8. What this does not fix

Stated plainly, because the proposal is worth less if it is oversold.

**Corpus gaps.** Ten of 108 products have a benefit table. Reasoning cannot
invent a limit that was never published, and 4,563 of the real corpus's failing
cases are figure cases. Better selection finds what exists faster; it adds
nothing. Extracting schedules from PDF layout is a separate and probably larger
piece of work.

**Review.** Every page in the served bundle is stamped `UNREVIEWED-eval-only`.
An accurate answer from an unreviewed page is an accurate quotation of
something nobody checked.

**The last mile of phrasing.** A local model still occasionally drops a figure
and falls back to the deterministic prose — correct behaviour, and visible as
`fell_back: dropped a resolved figure`. That is a generation problem, not a
selection one, and this proposal does not address it.
