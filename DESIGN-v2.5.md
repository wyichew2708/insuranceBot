# v2.5 — The steps to the real answer, what binds stays, and both questions answered

Four rules from the product owner (2026-09-05), each a reply to a measured
failure class in `DESIGN-v2.4.md`'s residue of 370 cases:

1. **Numeric binding.** Use a generic reply rather than refuse — unless the
   customer asked who can buy, where the age requirement is the answer.
2. **Answered when it should not have.** Change the answer: guide the customer,
   generically, to how they get the real answer.
3. **Answerability refusals.** Also give the guidance — step by step, so the
   customer can get the answer themselves.
4. **Several questions or intentions in one turn.** Do not limit the turn to
   one handler: call the handlers it needs and consolidate their replies.

## What was true before

The gate-failure path had one shape: a blocked draft became *"I'd rather not
answer that from memory. I'm passing you to a colleague"*. Re-running every
failing single-turn case against the gate that decided it:

| what decided the turn | cases |
|---|---:|
| numeric-binding blocked a correct draft | 49 |
| answerability refused a page that exists | 59 |
| delivered an answer to a question about the customer's own record | 90 |
| a compound turn routed once, as its second half | 37 conversations |

A claims page that says *"notify us within 15 days"* was blocked because the
15 was a bare number in prose rather than a table cell. A price question with
no premium anywhere in the corpus was answered from the FAQ's description of
the plan. *"How much will I get back?"*, two turns after *"I want to cancel"*,
was a limit question by its words and was answered from a promotion. And
*"what does X cover and how much does it cost?"* was routed once, as price,
refused as price, and the half the corpus answers well was never asked.

## What changes

### 1. A generic reply where a figure will not bind — `pipeline._strip_unbound`

Where numeric-binding is the *only* gate blocking a deterministic draft, the
lines that carry an unbound figure are dropped, the rest is delivered, and a
pointer says where the exact figures are (the policy wording, on the plan's
page). Lines are found from the figure's position in the text, not by
searching for its digits: a span the gate read across a line break names two
lines, and a bare "3" searched for would name every line with a 3 in it. The
claims those lines made go with them; if nothing substantive is left, rule 3
applies instead.

Two things are deliberately not trimmed. A **model's** draft with an unbound
figure is a model that invented one, and the rest of its draft is not trusted
line by line: it is refused as before, and the customer gets the steps (rule
3) rather than a colleague. And a **who-can-buy** question keeps its ages: an
unbound figure in an age sentence is bound to the eligibility page the
sentence came from (`_bind_ages`), and the gate re-reads that page to confirm
it says so — the same check a product-page figure gets.

One correction to the gate itself. A benefit the customer named by its number
— *"section 99"* — is an identifier the answer may echo (*"the pages do not
address section 99"*), not a figure the answer asserts. The gate now counts
the benefit codes the question named as bound text, read the way the
answerability gate already reads them, and nothing else about the question.

### 2. Guidance — `api.guidance`

One step list per topic: quote, refund, payment, policy record, application,
claims, claim status, cancellation, renewal, documents, eligibility, how to
buy, contact, and a generic fallback. Each is written once; the addresses come
from the destination registry the owner supplied, the plan's own page from its
channel binding, and the plan's published documents from the raw files the
catalogue tags to it — the same tagging retrieval is scoped by. Nothing comes
from retrieval and nothing from a model.

A guide names the product, and that name is a claim bound to the product's
page, so reference-integrity has something to check. It asserts nothing about
cover, so the coverage gates have nothing to check, and it carries the
`guidance` flag so the answerability gate does not hold it to a requirement it
was never trying to meet. It carries no digits: steps are bulleted, and a
document address, which carries its upload date, is offered as a structured
link rather than spelt out.

Every guide is a **handoff with steps**. The flag is what the contract and the
evaluation read; the steps are what the customer follows.

The same table now answers the tier-1 account-state turns that v2.3.1 routed
with one sentence and two links. A fraud report keeps its safety line first.

### 3. Where each rule lands in the turn

- **Before retrieval.** A refund follow-up after a cancellation turn (*"how
  much will I get back?"* within three turns of *"cancel"*) is read as
  `payment` and guided. The customer's own record and own money classify as
  account state: policy number, expiry, status, beneficiary, own documents,
  application progress, agent, personal data, premium increases, charges.
  Operating hours and a person by role go to contact. Recommendation
  phrasings (*"which insurance is suitable for me"*) cross the advice boundary.
- **After composition.** A price turn with no bound figure labelled premium,
  price or cost is the quote steps, whatever the FAQ said.
- **After the gates.** Numeric-binding alone: trim and deliver (rule 1).
  Numeric-binding or answerability: the topic's steps (rule 3). Any other
  gate: the draft was faulty, which is not a thing to reshape, and that is
  still a handoff.

### 4. Several questions in one turn — `api.split`, `pipeline._consolidate`

The split is conservative: at a conjunction followed by a word that starts a
question (*"and how"*, *"and what"*, *"and can"*), at a semicolon, or at a
question mark with more question after it; and only when the halves read as
different intents or each carries its own interrogative. *"Does it cover flood
and fire?"* stays whole. The parts are answered in order, each through the
full pipeline with the earlier parts as history — so *"how much does it cost"*
after *"what does X cover"* is about X, by the same mechanism the conversation
suite already exercises. The replies are joined; claims, figures and
destinations are the union; every part's gate verdicts travel on the envelope;
a handoff only where every part handed off; delivered where any part was; and
the trace records `parts` and each part's three-layer route.

## Acceptance

Every case-level loss against 1341/1711 is explained; `product_fact` does not
fall; the seed gate's failing set does not grow; and the safety tests keep
their teeth — a model's invented figure never reaches the customer, a fraud
report never receives the portal, a number the pages do not state is never
asserted.
