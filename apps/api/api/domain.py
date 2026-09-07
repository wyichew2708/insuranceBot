"""Whether a question is about insurance at all.

The answerability gate decides whether *this corpus* can settle a question.
It deliberately lets an unrecognised intent through, on the reasoning that
"most questions are broad, and refusing one for being broad is worse than
answering it broadly". That reasoning holds for every question a customer of
an insurer actually asks. It does not hold for "what is the capital of
France".

Measured on the real corpus before this module existed, with the deterministic
composer:

    what is the capital of france   → business-owners-super-suite/cover,
                                      "alterations, additions and improvements
                                      ... Subject to a maximum limit of 10% of
                                      Sum Insured", delivered
    who won the election            → travel-infinite/eligibility, the whole
                                      definitions block, delivered
    give me a recipe for chicken    → home-insurance/exclusions, "We will not
                                      pay for any loss, damage or injury",
                                      delivered

None of those scored well — the recipe matched at 0.117 against a 0.45
confidence floor. They were delivered because a low score routes to the RAG
fallback, and the fallback answers from the best section it can find rather
than from a section that is any good. A bag of words always has a nearest
neighbour; the question is whether the nearest neighbour means anything.

So this is a *positive* test, not a score. The corpus is one insurer's product
documentation, and a question about it says so somewhere: it names a plan, or
carries a shape `classify` recognises, or uses one of the words the business
runs on. A question with none of those is not a question we answer badly — it
is not a question for us.

The test errs towards saying yes. A false "in domain" leaves the turn exactly
as it was before this module existed; a false "off domain" refuses a customer
who asked something real, which is much the worse trade. Every signal below is
therefore generous, and the lexicon includes plenty of words that are only
insurance-adjacent — "mortgage", "spouse", "hospital" — precisely because the
customer who uses one of them is talking to the right company.
"""

from __future__ import annotations

import re

from harness import GroundedAnswer
from harness.ask import Ask
from harness.contracts import Link
from harness.gates import ADVICE_SEEKING_RE
from harness.intent import Intent, classify, smalltalk_kind

from api.guardrails import medical_emergency
from api.retrieval import PRODUCT_HEAD_WORDS, named_products, subject_terms
from api.route import FRAUD_RE
from api.router import _INCIDENT_FAMILIES, INCIDENT_RE
from okf import DESTINATIONS, Bundle, Desk, term_idf

#: The words the business runs on. Not a taxonomy and not derived from the
#: corpus — a corpus-derived list would contain "capital", which is how the
#: capital of France came to be answered from a business-interruption page.
#:
#: Three kinds of word are here. The trade's own vocabulary (premium, excess,
#: underwriting, sum insured), which nobody uses by accident. The things
#: insurance is *about* (hospital, accident, burglary, baggage, mortgage),
#: which a customer describing their situation will reach for before they
#: reach for a product name. And the handful of service words that mean the
#: customer is talking to their insurer rather than to a search engine — an
#: OTP, a portal login, an adviser.
DOMAIN_RE = re.compile(
    r"""\b(?:
      insur\w* | takaful | underwrit\w* | assur\w* | reinsur\w*
    | premium\w* | policy | policies | polic(?:yholder|ies) | coverage | covered | covers?
    | benefit\w* | payout\w* | pay\s?out | claim\w* | excess | deductible\w*
    | rider\w* | endorsement\w* | beneficiar\w* | nominee\w* | nominat\w*
    | exclusion\w* | exclude[sd]? | waiting\s+period | pre.?existing
    | pre.?authoris\w* | pre.?authoriz\w* | sum\s+(?:insured|assured)
    | cash\s+value | surrender\w* | maturity | matures? | endowment | annuity
    | free.?look | cooling.?off | grace\s+period | co.?payment | co.?insurance
    | no.?claims?\s+discount | ncd | protection | plan\w* | tier\w*
    | hospital\w* | clinic\w* | doctor\w* | surger\w* | surgical | medical
    | illness\w* | disease\w* | diagnos\w* | symptom\w* | treatment\w*
    | ambulance | emergenc\w* | chest\s+pain\w* | accident\w* | injur\w*
    | disab\w* | dismember\w* | death | died | passed\s+away | funeral
    | critical\s+illness | terminal | dread\s+disease
    | renew\w* | lapse\w* | reinstat\w* | cancel\w* | terminat\w*
    | quot\w* | premium | instal\w?ment\w* | giro | deduction\w*
    | motor | vehicle\w* | car | motorcycle\w* | motorbike\w* | windscreen
    | third.?part\w* | liabilit\w* | negligence
    | travel\w* | trip\w* | baggage | luggage | suitcase | flight\w* | airline\w*
    | overseas | abroad | itinerar\w*
    | home | house | flat | hdb | condo\w* | apartment | renovat\w* | contents
    | fire | flood\w* | burglar\w* | theft | stolen | steal | robbed | robber\w*
    | maid\w* | helper\w* | domestic\s+worker | fdw | confinement
    | pet\w* | dog | cat | vet\w*
    | mortgage\w* | loan\w* | dependant\w* | dependent\w* | spouse | husband
    | wife | child(?:ren)? | kids? | famil\w* | retire\w* | savings? | invest\w*
    | workmen | work\s+injur\w* | employee\w* | employer\w*
    | otp | portal | tiqconnect | log\s?in | password | adviser\w* | advisor\w*
    | agent\w* | broker\w* | etiqa | tiq
    )\b""",
    re.I | re.X,
)


def in_domain(bundle: Bundle, text: str, ask: Ask | None = None) -> bool:
    """Whether anything in the question says it is about insurance."""
    if classify(text) is not Intent.unknown:
        return True
    # The customer's own words this turn, never a product carried in from an
    # earlier one. "Cool, anyway what do you think about the weather lately"
    # after a travel question still carries travel, and answering it from the
    # travel pages is exactly the failure: a conversation that has been about a
    # product does not make the next thing said a question about it.
    if ask is not None and ask.named:
        return True
    if named_products(bundle, text):
        return True
    if DOMAIN_RE.search(text):
        return True
    if ADVICE_SEEKING_RE.search(text) or FRAUD_RE.search(text):
        return True
    if INCIDENT_RE.search(text) or any(pattern.search(text) for pattern, _ in _INCIDENT_FAMILIES):
        return True
    tokens = set(re.findall(r"[a-z0-9]+", text.lower()))
    return bool(tokens & PRODUCT_HEAD_WORDS)


def off_domain(bundle: Bundle, question: str, ask: Ask | None = None) -> bool:
    """Whether this question is outside the corpus's subject altogether.

    Two conditions, and both must hold, because either alone is wrong.

    Nothing in the question is about insurance — `in_domain` above. On its own
    that refuses "what do I have to declare?" and "how much will it be?",
    ordinary follow-ups whose words happen to miss every list. So it is paired
    with a second test: the question names something *the corpus has never
    heard of*. France, an election, a recipe, bitcoin, a labrador. Those are
    the words that say the subject is elsewhere, and the pairing is what tells
    them apart from a follow-up phrased in words the documentation itself
    uses.

    The second test is deliberately the corpus's own vocabulary rather than a
    list, so it needs no maintenance as products come and go: a term the
    documentation uses is a term customers may ask about. It is also why the
    first test cannot be dropped — a customer describing a real incident will
    name a place or a body part the corpus has never seen ("the airline lost
    my suitcase in Tokyo"), and it is the incident, not the vocabulary, that
    makes that a question for us.

    A question with no content words at all — "yes", "why?", "车险多少钱" —
    is off domain for a third reason: there is nothing in it to answer. The
    reply is the same, because in every one of these cases the useful thing is
    to say what this can help with and offer a person, rather than to guess.
    """
    text = (question or "").strip()
    if not text:
        return True
    # Handled before this, and on their own terms. A greeting is not an
    # off-topic question, and neither is someone describing chest pains.
    if smalltalk_kind(text) or medical_emergency(text):
        return False
    if in_domain(bundle, text, ask):
        return False
    terms = subject_terms(text)
    if not terms:
        return True
    return any(term not in term_idf(bundle) for term in terms)


#: What the customer is told. Says what this can and cannot do, in that order,
#: and ends on a person — the same two desks the guidance registry uses, so a
#: turn that falls off the corpus lands somewhere real rather than on an
#: apology. No product is named, because none was asked about.
DECLINE = (
    "That one's outside what I can help with — I answer from Etiqa and Tiq's "
    "published insurance documents, so I can tell you what a plan covers, what it "
    "excludes, who is eligible, and how to buy or claim.\n\n"
    "Ask me about any of those and I'll take it from there. If you need a person, "
    "our team can help with anything else: {contact}"
)


def decline(bundle: Bundle) -> GroundedAnswer:
    """The reply to a question that was not about insurance.

    Carries no claims, because it asserts nothing about a product — there is
    nothing here for the provenance gates to check, and `gate_domain` will
    stop it being counted as an answer. It is a handoff: the customer asked
    something real, just not of us, and the honest end of the turn is a person
    rather than a paragraph assembled from the nearest policy page.
    """
    contact = DESTINATIONS[Desk.contact]
    return GroundedAnswer(
        answer=DECLINE.format(contact=contact.url),
        handoff=True,
        off_domain=True,
        confidence=1.0,
        destinations=[Link(label=contact.label, url=contact.url)],
    )
