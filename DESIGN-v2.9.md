# v2.9 — Controlled agentic RAG: what v2.8 already is, and what it is not yet

A proposal, not yet built. It answers a design review that recommended, for a
customer-facing product-enquiry assistant in 2026, a *controlled agentic RAG*
architecture — channels → conversation layer → input guardrails → intent and
entity understanding → an orchestration harness with a query router that
sends knowledge questions one way and tool/API questions the other →
hybrid/graph retrieval and enterprise APIs → a composing model → answer
verification with a repair branch → output guardrails — surrounded by tracing
and a closed-loop evaluation pipeline, and explicitly *not* a swarm of
per-topic agents.

Read against v2.8 as built, most of that review describes the system. The
useful part of this document is the residue: the four places the review names
that v2.8 does not have, the three places where it recommends something v2.8
deliberately does the other way, and the order to build the residue in without
losing a case.

An earlier cut of this review was written against `main`, which stops at v1.
It is withdrawn; nothing in it survives contact with `DESIGN-v2.3.md` through
`DESIGN-v2.8.md`.

---

## 1. Layer by layer

Eight layers in the review; what v2.8 has for each; what it does not.

| Review layer | v2.8 has | Not yet |
|---|---|---|
| **Channels** | one API (`POST /v1/answer`, `/v1/answer/stream`), three surfaces (console, studio, `/chat`), a session bound to a route to market rather than a brand | channel adapters (app, WhatsApp, voice) — transport, not design |
| **1 Conversation** | client-supplied `history`, server-side `SessionMemory` with a per-turn summary, reference resolution (*"what's the coverages"* after *"term life"*), suggestion chips, structured `destinations`, stage-level streaming | **language** — the field test's Malay turn is kept in domain and answered in English with a refusal; nothing downstream reads the language. **Cards** for a comparison or a quote |
| **2 Input guardrails** | a rule layer that always runs and a model layer that may only raise risk, weighed per category with a noisy-OR, PII redaction before memory or trace, third-party and distress routing, injection refused before retrieval; backtest 0 false positives on 27,718 held-out questions | nothing the measurement asks for |
| **3 Understanding** | the `Ask` (product by name index, lexical intent over 19 intents, benefit subject, scope, kind), the model as a closed-set product selector with lexical fallback, abbreviations, incident → line, compound split | **entities beyond product and benefit** — age, destination, dates, duration, tier, sum insured. Nothing extracts them because nothing consumes them; the tool path below is the consumer |
| **4 Harness** | a three-layer router decided once and written to the trace; budgets with a defined exit; per-stage trace; compound turns fanned out and consolidated | **the handlers are branches of one 1,600-line function.** The router is explicit; the workflows it routes to are not. **No tool/API path** beyond the policy-summary stub |
| **5A Knowledge** | three retrieval layers (typed graph with containment and reverse edges, OKF frontmatter filter, dense over wiki and raw fused by RRF), requirement-driven `holds_answer` steering, product-scoped retrieval, page/section parent-child, the product FAQ always loaded, confidence and vector floors, RAG fallback only when needed | **a benefit-level graph.** Edges are page-to-page; the review's incident question (*flight cancelled by weather, need a hotel — which benefits apply?*) crosses cause → insured event → benefit → limit → exclusion → evidence, and only the benefit and exclusion nodes exist today. **The dense layer is unmeasured** (open since v2.3) |
| **5B Enterprise tools** | `sor.policy_summary` behind an entitlement predicate; a destination registry and a guidance table that send the customer to the portal, the quote page, claims and a person | **quote, eligibility, claim status, policy detail as tools.** Every one of these is guided or handed off today, and the conversation dataset scores that as correct because the contract says so |
| **6 Composer** | deterministic composition from loaded sections, every figure bound to a row, a quotation or an SOR field; a model rewrites prose only, under an acceptance test that rejects a dropped figure | composing from retrieved raw clauses (their K4) — a historic-version question is blocked and handed off rather than answered from that version's wording |
| **7 Verification** | twelve gates, all run on every turn: provenance (reference-integrity, numeric-binding, version and channel coherence, exclusion-completeness, advice-boundary, groundedness with an entailment judge, supporting-sources) and aboutness (answerability, domain, entitlement-assertion, about-the-ask); typed `missing` on a failed gate; soft failures trimmed or replaced with the steps to the answer | the review's *retrieve again* branch — deliberately not built, see §2 |
| **8 Output guardrails** | a model-and-rules output screen against the draft's own evidence, recorded as a gate; the render substitutes contacts from the channel binding; advice and entitlement re-checked on the answer text | language match |
| **Observability and evals** | per-stage trace with rejected candidates, the console, seven suites, a 1,711-case conversation dataset scored turn by turn, a generated suite that grows with the corpus, `diff_runs.py` and the zero-lost rule, `known-findings.json` guarding both directions | **Langfuse export** (shape is right, nothing ships it); a **selection-accuracy** row and a **clarification-rate** row as first-class report lines (`DESIGN-answering.md` §6 asked for both; the layer-2 breakdown and *asked which product instead of handing off* are the nearest things) |

Two rows deserve a second look because they are where a reader of the review
would expect a gap and there is none.

**The review's verifier list is a subset of the gates.** Groundedness,
citation verification, numerical consistency, contradiction detection and
evidence sufficiency map to groundedness, reference-integrity plus
supporting-sources, numeric-binding, exclusion-completeness plus the judge's
`contradicts` verdict, and answerability plus the floors. The one item it
names that has no gate — a *product-rule validator* — is content here rather
than code: *"pre-existing conditions are excluded unless the add-on was
bought"* is a sentence on an exclusions page, and exclusion-completeness
refuses any coverage assertion made without that page loaded. A rule engine
would be a second place for the same fact to live.

**The review's "numbers are never generated" is stronger here than it asks
for.** It asks that facts come from structured sources and the model only
quote them. v2.8's composer does not ask the model for the prose *either*
until every figure is bound; the model's rewrite is then rejected if a bound
figure went missing. Measured across 25,791 cases on the real corpus: 0
unbound figures.

---

## 2. Where the review and v2.8 disagree, and which way to go

Three recommendations run against decisions v2.8 made on a measurement.

### 2.1 "The model should decide which controlled workflow to use"

v2.8's router is deterministic and lexical, and that is a decision, not a
gap (`api/router.py` says why: it decides whether a customer is answered,
asked or handed on, and that must be reproducible, free and identical on
every machine). The model's part is bounded to selection from closed sets —
which product, whether the question is about insurance at all, whether a
claim is entailed — and every one of those falls through to the deterministic
path on absence, timeout or an id it was not offered.

Keep it. The review's own reason for the harness — *the model should decide
which workflow, rather than being allowed to freely invent how to answer* —
is satisfied more strictly by a router the model cannot steer than by one it
drives. What the review is right about is that the *workflows* should be
things: declared, inspectable, with a contract. That is §3.1.

### 2.2 LangGraph as the workflow engine

Adopt it when a handler needs a model-chosen sequence of tool calls. None
does. Every path in v2.8 is a fixed sequence, and the tool path proposed in
§3.3 is fixed too (resolve entities → call the tool → bind the fields → gate).
A graph engine buys checkpointing and branching for loops that do not exist
here, and its cost — a runtime that is not the deterministic path CI and every
eval run depend on — is paid on every turn. The trace already records every
stage and every rejected candidate, which is the observability a graph engine
would be adopted for.

If a loop appears (a repair branch that re-retrieves, a multi-tool quote), the
handler registry in §3.1 is the seam to put it behind.

### 2.3 "On FAIL: retrieve again"

Built once and taken out (`DESIGN-answering.md` §7, step 2). The gate failures
a repair loop would recover all had one cause — a child page loaded without
its product's typed edges — and following the edges at retrieval time removed
them. The stance stands: a check exists so that coverage is asserted *in the
presence of* the exclusions, and a loop that loads the page and re-gates makes
the check vacuous. The loop stays unbuilt until a failure appears that it
would fix; `GateResult.missing` is typed so that when one does, the caller has
the page ids without parsing English.

The review's other FAIL branches — clarify, human — are the clarification
turn and the handoff, and both are measured.

### 2.4 Where the review is simply right

**No swarm.** Agreed and never built. The *"fanned out to several agents"* in
the v2.8 pull request is `api.split` and `_consolidate`: one compound turn
answered as its parts through the same pipeline. It is not a set of agents and
should not become one.

**Vectors are recall, not a bypass.** Already the rule: a page found by
similarity is a candidate under the same filter, composition and gates as one
found by words, asserted by four tests at similarity 0.99.

**Evaluation infrastructure outside the runtime, closing a loop.** Built;
`EVALUATION.md` is the loop. What it lacks is named in the last row of §1.

---

## 3. What to build, in order

Each item ships on its own. Each is held to the rule every version since v2.4
was held to: **zero cases lost** on the 1,711-case conversation dataset, no
newly failing case in the seed gate, numeric binding at 100.0%, entitlement
leaks at 0, guardrail backtest at 0 false positives. Where an item changes
what a correct reply *is*, the dataset's contract changes with it, in the same
change, and the diff says which cases moved and why.

### 3.1 Make the workflows things — a handler registry

`_answer_turn` routes explicitly and then handles implicitly: smalltalk,
entity, directory, emergency, off-topic, account state, advice, browse,
clarify, the retrieve-and-compose path, the price fallback, the soft-failure
paths. Each is a workflow in the review's sense and none is declared as one.

Extract them to `api/handlers/`, one module per layer-1 kind and one per
layer-3 handler that has its own behaviour, behind one contract:

```text
Handler
  kind            Layer1 | Layer3 it owns
  evidence        which sources it may load: wiki · raw · sor · registry · none
  budget          pages, tool calls, wall clock — the Budget it is charged
  gate_profile    which gates may soft-fail into a trim or a guide
  run(turn) -> GroundedAnswer
```

The pipeline becomes: screen → read the Ask → route → look up the handler →
run → screen → gate. Nothing about any answer changes. The proof is the one
the suite already gives: the conversation suite is deterministic and produces
byte-identical answers forwards and reversed, so the refactor is accepted when
all 1,711 answers are byte-identical to v2.8's.

What it buys: the trace names the handler as well as the layers; a handler's
allowed evidence is enforced rather than assumed (the tier-1 account-state
path can be *proved* to load no pages); and §3.3's tool handlers have a place
to be registered instead of a branch to be added to.

### 3.2 Understanding v2 — entities, and the language of the turn

Two additions to the `Ask`, neither changing an answer until §3.3 consumes
them.

**Entity slots.** `age`, `destination`, `trip_start`, `trip_end`,
`duration_days`, `tier`, `sum_insured`, `vehicle`, `occupation` — the values a
quote or an eligibility check needs. Read deterministically where the shape is
unmistakable (an age, a date, a duration) and by the model where it is not,
under the same three constraints `api/understand.py` puts on product
selection: a typed schema, validation against what the value can be (an age
is an integer between 0 and 120, a destination resolves against the bundle's
`destinations` vocabulary), and fall-through to *unset* on any failure. An
entity is carried in `SessionMemory` the way the product is, so *"and for
twelve days?"* keeps the destination from the turn before.

**Language.** Detect it at the gateway, deterministically (a small lexicon
first; a model as a second opinion where configured, never as the only vote).
Where the corpus carries the language, retrieval filters on it; where it does
not — and today it carries only English — the reply says so in the customer's
language and offers a person, in place of an English refusal that reads as a
broken bot. Measured on the field test's Malay case, then on a dozen
authored Malay and Chinese turns added to `field-test.yaml`.

### 3.3 The tool path — quote, eligibility, claim status, policy detail

The review's whole right-hand column. Every one of these is guided or handed
off in v2.8, and the guidance is correct until the system that holds the
answer can be asked. The design principle does not change: **the system of
record decides the fact; the composer binds it; the gates check the binding.**
`Figure.sor_field` already exists for exactly this and is already checked by
numeric-binding. What is missing is the tools that produce such figures.

`api/tools/`, one module per system, all behind one envelope:

```text
SystemOfRecordResult
  source        quote-api | eligibility-rules | policy-api | claims-api
  as_of         when the system answered
  reference     the quote reference, the claim reference, the policy id
  fields        name -> value, each bindable as Figure.sor_field
  entitlement   what the session had to hold to see it
```

| Tool | Decides | Session needs | Dev / CI | When absent or down |
|---|---|---|---|---|
| `quote` | the premium for a product, tier and the entities in the Ask | nothing — a quote is public | a fixture with a table of premiums per (product, tier, band) | the quote steps (today's reply) |
| `check_eligibility` | whether the customer's age, residency and occupation qualify | nothing | rules evaluated in-process from a structured table (below) | the eligibility steps |
| `claim_status` | where a named claim is | authenticated, and the claim is theirs | fixture | the claim-status steps |
| `policy_detail` | the customer's product, version, tier, dates, people covered | authenticated | `sor.FIXTURE_POLICIES`, extended | the policy-record steps |

Three things follow from the principles already in force.

**Eligibility is a table, not a paragraph.** Eight eligibility cases were
found passing on a vaccination paragraph and a deductible definition
(`DESIGN-v2.5.md`); the fix named was *an eligibility page that states the
age*. The stronger fix is the one benefit tables already are: a
`raw/eligibility-tables/<slug>.csv` with rows for entry age, maximum age,
residency and occupation classes, compiled from the product summaries and
wordings by the same extraction that lifts a schedule of benefits, lint-checked
the same way, and bound the same way — an age in an eligibility answer binds
to its row, and `_bind_ages` retires. `check_eligibility` is then a rules
evaluation over rows, deterministic, with nothing to fall back to because
there is no model in it.

**The tool's figures are the only figures.** A quote answer carries the
premium the tool returned, bound to `sor_field=quote.premium`, and nothing
else numeric; the composer's sections are loaded for the product's cover and
exclusions as today, so the answer can say what the price buys. `_priced`
already refuses a price answer with no premium-labelled figure; with a tool it
passes on the tool's figure and on nothing else.

**Entitlement is a predicate on the session, never on the answer.** The
`entitlement-assertion` gate skips on an authenticated session because the
system of record is then the authority. That stays exactly as it is: a tool
that needs authentication raises `NotEntitled` on an anonymous session and
the handler falls to the guidance reply, which is what the customer gets
today.

The dataset's contract moves where the tool exists: a `price` turn on an
authenticated session with a configured quote tool expects a bound premium;
the same turn anonymous, or with no tool, expects the quote steps. Both are in
the suite; the fixture tool runs in CI; the report gains a `tool` column
beside `layer3`.

### 3.4 A benefit-level graph, for the question that crosses relationships

The review's example — *"my flight was cancelled by bad weather and I need
to book another hotel, which benefits could apply?"* — is answered in v2.8 by
reading the incident for its line, asking which travel plan, and then
answering a coverage question from that plan's pages with the exclusions
loaded. That is correct and it is not the answer the customer wanted, which
is a *list* of benefits, each with its limit, its relevant exclusion, and
what they would need to claim.

The nodes mostly exist. Benefit codes are the rows of the benefit tables and
the entries of `vocabulary.yaml`; exclusions are sections of the exclusions
page; the documents a claim needs are on the claims journey page. What does
not exist is the edges between them, or the two node kinds that make the
question askable: a **cause** (weather, strike, illness) and an **insured
event** (cancellation, curtailment, delay, missed connection).

Add three edge kinds to `EdgeKind` — `covers` (event → benefit), `excludes`
(exclusion → benefit) and `requires` (benefit → evidence) — and one source of
truth for the cause → event pairs: an authored table in `okf.yaml`, the way
destinations and channels are authored, because a cause is a small closed
vocabulary and a table a product owner can read is worth more than an
extraction nobody reviews. Benefit → exclusion and benefit → evidence edges
are extracted at compile from the exclusions and claims sections under the
same quotation binding the LLM WIKI pass uses, with `confidence`, and used
for **retrieval only**: an extracted edge decides which sections load, never
what the answer asserts, which the gates decide from the sections as they do
now.

The handler (`incident_coverage`, registered in §3.1) walks cause → events →
benefits for the resolved product, loads each benefit's row, its exclusion
section and its evidence section, and composes a benefit-per-paragraph
answer — every figure bound, every exclusion present, every claim from a
loaded section. Twelve gates as today. Measured on a new conversation
archetype, `incident-multi-benefit`, authored across the travel and home
lines.

### 3.5 Two intents the router does not have

Found while mapping the review's enquiry table onto the handlers.

**Compare.** *"Compare Entry with Luxury"* names two products, and the clarify
policy since v2.2 is that two full names in one turn asks which was meant.
For a comparison that is the wrong reply. A `compare` intent (two named
products, or two tiers of one, and a comparative verb) routes to a handler
that puts the benefit-table rows side by side — every figure bound, nothing
composed — and returns them as a structured `table` on the answer alongside
the prose, so a client can render a card. Where either side has no benefit
table, the reply says which and points at the pages.

**Recommend.** Already handled — the adviser referral before retrieval
(`DESIGN-v2.8.md` §2) — and worth naming in the table because the review
lists it as *needs analysis plus deterministic recommendation rules*. Not
here: a recommendation is a licensed adviser's call, and a rule that produces
one is advice with a different author.

### 3.6 Guide the customer — a greeting map, a browse tree, journey-aware chips

The suggestion chips, the directory, the clarification options and the guidance
destinations already steer a customer one hop at a time. What is missing is the
first hop and the path between hops: the greeting is a paragraph, the starters
are product names written into code, chips vanish on a handoff, and the
customer learns what the bot cannot do only by asking.

`GUIDANCE-MAP.md` is the full reference, generated from the bundle: six
branches on the greeting (product information, claims, my policy, buy and
quote, promotions, help and contact), the line → plan → topic-ring browse
tree for all 37 plans, a journey-ordered next-chip table per intent, and the
coverage matrix that shows, per plan, which ring topics the pages hold.

Three rules carry over from the chips as built. Every node is a literal
question the corpus answers or a registry destination, generated at load
time and never model-written, so a tapped chip is a delivered answer by
construction. No node carries a digit. A question node is offered only while
its page is approved and in its window; a topic the pages lack is omitted,
and the reply falls to the guidance steps.

Two changes to the runtime. Chips stay on guidance and handoff replies,
restricted to the plan in play, so a refusal ends with a way to stay in the
conversation. Next-topic order follows the journey sequence in the
conversation taxonomy, and the session memory's per-turn intents suppress a
topic already answered — the first consumer of the memory on the request
path, and deterministic.

Proved by a generated suite that asks every chip the map and the per-turn
tables offer and asserts each reply is delivered, and by two report rows:
dead-end rate (turns ending in a handoff with no chip) and chip coverage
(plans whose overview offers at least three topics).

### 3.7 Conversation memory — typed state, not a prose summary

Not a token problem: the model sees at most the last three questions, only
in the product-resolution call, and the rest of the turn reads the history
deterministically. What the memory lacks is a consumer and a bound. The
rolling prose summary is written on every turn and read by nothing on the
request path; the session file grows without limit. So: carry the typed
state (product, intent, and the §3.2 entity slots) as the recalled record and
retire raw-question recall where a slot answers the same need; keep the last
N turn records and expire idle session files, which is retention rather than
summarisation and matters more for a PII-sensitive deployment than for
tokens; keep the background model refinement as it is, off the request path,
once the summary has a reader — the handover payload is the obvious one.

### 3.8 Close the open measurements

Three items already on the list, restated so they are not lost behind the
new ones. **Measure the dense layer** on the GPU host; `VECTOR_FLOOR`,
`VECTOR_RAW_FLOOR` and `DENSE_SECTION_WEIGHT` are settings because the
numbers that should set them have never been produced. **Ship traces to
Langfuse**; the shape is right. **Add the two report rows** from
`DESIGN-answering.md` §6 — selection accuracy (was the right product chosen,
apart from whether the answer was right) and clarification rate — so that
§3.2 and §3.5 can be judged on the numbers they are about.

---

## 4. What this does not change

The invariants from `DESIGN-answering.md` §5, restated because every item
above was checked against them:

1. No number reaches an answer without a benefit-table row, an eligibility
   row, a verified quotation or a system-of-record field.
2. The model never establishes a fact. It selects from closed sets — a
   product, an entity value, an entailment verdict — and phrases what was
   established.
3. The gates remain authoritative and post-hoc; a tool's figure is gated like
   any other.
4. The deterministic path stays complete: every tool has a fixture, every
   model call a fall-through, and CI runs with none of them configured.
5. Every claim keeps its source, and a URL handed to a customer comes from a
   registry, never from retrieved text.
6. A model layer may raise the risk of a turn and never lower it.

---

## 5. Acceptance

| Item | Proved by |
|---|---|
| 3.1 handler registry | 1,711 answers byte-identical to v2.8's; the trace names a handler on every turn |
| 3.2 entities, language | entity fixture (≥ 100 labelled turns) ≥ 0.9 slot accuracy; field test Malay case passes; conversation dataset zero lost |
| 3.3 tools | conversation dataset zero lost; new authenticated quote, eligibility, claim-status and policy cases pass against the fixtures; entitlement leaks 0; every tool-down case returns today's guidance |
| 3.4 benefit graph | `incident-multi-benefit` archetype ≥ 90% whole conversations; exclusion-completeness and numeric-binding unchanged at 100% |
| 3.5 compare | compare cases return a bound table; the two-names clarification still fires where no comparative is present |
| all | seed gate no newly failing case; guardrail backtest 0 false positives; `known-findings.json` shrinks or holds |
