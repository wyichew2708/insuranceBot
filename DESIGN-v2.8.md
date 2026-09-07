# v2.8 — What it will not answer

Four kinds of turn that were being answered from the corpus and should not
have been.

## What was true before

Every version up to here made the bot better at answering. This one is about
the questions it should decline, and it starts from a finding rather than a
plan.

The curated suites run in CI against the **seed** bundle — a few fixture
pages. `field-test.yaml`, the 109 cases four testers produced by asking the
way customers ask, carries no `bundle:` marker, so it runs there too. Run
against `okf-real`, the corpus the product actually ships, it scored **86/109**
and the failures were not the ones the seed run showed. Four of them:

```
what is the capital of france   → "We extend to cover alterations, additions
                                   and improvements ... Subject to a maximum
                                   limit of 10% of Sum Insured", delivered
should i buy bitcoin or tesla   → four paragraphs of reinstatement conditions,
                                   then "I'll connect you with a licensed
                                   adviser", delivered
mediacl insurance               → "I could not establish that from our approved
                                   product pages. Let me pass you to a
                                   colleague who can confirm it."
policy number for John Tan      → "Log in to the customer portal ... Open My
                                   Policies", delivered
```

Each came with the full apparatus of a real answer behind it: pages cited,
figures bound, gates passing. Nothing was hallucinated. The corpus really does
say those words. They were simply not answers to those questions, and three of
the four were delivered as though they were.

The mechanism is the same each time. Retrieval always has a nearest neighbour:
a bag of words scored against 37 products returns *something*, and where the
score is poor the RAG fallback answers from the best section it can find
rather than from a section that is any good. "Recipe for chicken rice" matched
`home-insurance/exclusions` at 0.117 against a 0.45 confidence floor and was
answered anyway. `gate_answerability` did not stop it, by design: it lets an
unrecognised intent through, because "refusing a broad question is worse than
answering it broadly". That reasoning holds for every question a customer of
an insurer asks. It does not hold for the capital of France.

## What changes

### 1. A domain test — `api.domain`, `harness.gates.gate_domain`

A twelfth gate, and a route before retrieval. A question is **off domain**
when two things are true together, and neither alone is the rule:

*Nothing in it is about insurance* — it names no product, carries no shape
`classify` reads, describes no incident, seeks no advice, reports no fraud,
and uses no word from a curated lexicon of the trade's own vocabulary and the
things insurance is about.

*And it names something the corpus has never heard of* — France, an election,
a recipe, bitcoin, a labrador. That test is the corpus's own vocabulary rather
than a list, so it needs no maintenance as products come and go.

The pairing is what makes it safe. On the first condition alone, *"what do I
have to declare?"* and *"how much will it be?"* are refused: ordinary
follow-ups whose words happen to miss every list. On the second alone, *"the
airline lost my suitcase in Tokyo"* is refused: a customer describing a real
incident names a place the corpus has never seen, and it is the incident, not
the vocabulary, that makes it ours. Measured across the conversation dataset's
2,893 turns before a line of it shipped: **6 turns flagged, 0 of them in a
case that was passing.**

The product carried in from an earlier turn is deliberately not a signal. A
conversation that has been about travel does not make the next thing said a
question about travel — *"cool, anyway what do you think about the weather
lately"* was answered from the travel pages precisely because the scope was
still set.

The reply says what this can help with and offers a person. The gate blocks
it: a useful thing to send, and a false thing to record as an answer.

### 2. A recommendation is a person's job — `api.guidance.adviser_referral`

`gate_advice_boundary` already refused an unflagged recommendation, so *"should
i buy bitcoin or tesla stock"* was correctly undelivered — after retrieval had
composed four paragraphs of business-property conditions and appended the
adviser sentence to them. The boundary is a reason not to retrieve, not a
sentence to add to whatever retrieval returned. It now routes before retrieval
and replies with the referral alone.

`ADVICE_SEEKING_RE` gains the form a tester used that every verb in it missed:
*"should i cancel my policy with great eastern and move to etiqa"*, and
*"is X better than Y"*.

The line the referral must not cross is a statement of *need*. *"I need
insurance for my elderly parents"* is a shopper opening a conversation, and
the catalogue is the right reply; it is in the seeking pattern because
recommending for someone else's circumstances is regulated, which is a rule
about what an answer may say. `wants_a_recommendation` separates the two.
Measured: routing the need shapes to the adviser as well cost `need-parents`
and gained nothing.

### 3. A misspelt product is asked about — `api.clarify.did_you_mean`

The tie-breaking code carried a comment: *"A misspelt product we do carry does
not reach this — the model resolves 'trvael insurance' and sets a focus long
before the tie."* True where a model is configured. The deterministic path is
what CI, the eval suites and every offline deployment run, and there a single
transposed letter turned a product we sell into a refusal. The customer who
mistypes is the easiest customer to help and was getting the worst answer in
the system.

A `difflib` ratio of 0.8 over the catalogue's own name vocabulary, consulted
where `unsupported_term` already sits. One near miss is worth asking about
even though `clarification` needs two options: a tie of one is not a choice,
but a misspelling resolving to one product is a different thing. A word that
is a near miss of nothing — "crop", "kidnap", "aviation" — stays a refusal,
because a line we do not write should not become the nearest thing we do.

### 4. A named third party's record — `api.guardrails.named_third_party`

`THIRD_PARTY_RE` is documented as catching a request for *"a named third
party's"* data and matches no name: it wants "my friend's policy". *"What is
the policy number for John Tan"* reached the account guidance and was told how
to log in and open My Policies — the right answer for your own policy, and an
instruction to go looking for someone else's.

The check needs the catalogue, which the rule layer deliberately does not
have: "policy details for Invest Smart Vista" is capitalised exactly like
"policy number for John Tan", and only the bundle knows which is a product. So
it runs in the pipeline and reports a blocking entitlement finding the same
way a rule hit does.

## Measured

Three suites, deterministic composer, against v2.7.

```
                                          v2.7             v2.8
conversation dataset (okf-real)      1675/1711  97.9%  1680/1711  98.2%
  whole conversations                 333/355   93.8%   336/355   94.6%
  handoff contract                    174/179   97.2%   176/179   98.3%
  advice_boundary                      15/15   100.0%    15/15   100.0%
  owed a handoff, did not give one          5                 3

field test on okf-real                 86/109  78.9%     98/109  89.9%
seed gate (CI, make evals)             98/130  75.4%    105/130  80.8%
```

**Conversation: 5 gained, 0 lost. Field test: 12 gained, 0 lost. Seed gate: 7
gained, 0 lost.** Tests, mypy and ruff clean; golden 9/9.

The seed gate is still short of its 100% threshold and CI is still red there,
as it is on the base branch. Nothing was skipped, relaxed or quarantined to
move it: every one of the seven is a case that now behaves correctly.

### What remains

**On the conversation dataset, 31.** Two entity turns want the underwriter
named; four clarify turns expect a question where a plan is given. *"Why?"*
after a rejected claim and *"and my phone number"* mid-address-change are
still answered rather than handed off — both are turns whose meaning is
entirely in the turn before, and the domain test deliberately does not read
carried context.

**On the field test, 11.** Five are a section of prose that answers a
neighbouring question — a cancellation clause for a coverage question, a
group-plan paragraph for an ellipsis. Two are the wrong product for a
situation described without a line word ("my helper fell down the stairs at
home" reaches personal accident, not maid). One is a brand question answered
from a product page. One is a guarantee. One is *"yes"* on its own, which has
no subject and reaches the corpus's own vocabulary test as a word the corpus
uses.

The last is **Malay** — *"berapa harga insurans kereta"* is a car-insurance
price question, correctly kept in domain by `insur\w*`, and then answered with
"I could not establish that from our approved product pages" because nothing
downstream reads the language. A language layer is out of scope here and is
the clearest single gap the field test still names.

### A note on where the field test runs

`field-test.yaml` is written against `okf-real` and scored in CI against the
seed bundle, where a dozen of its cases assert product ids the seed catalogue
does not contain. That is why the two runs disagree, and it is worth fixing —
but marking the file `bundle: okf-real` would make `make evals` skip all 109
cases, which trades a wrong number for no number. The honest fix is to run it
against the corpus it was written for, as a second CI step, and that is a
change to the pipeline rather than to the bot. Left for the owner to decide.
