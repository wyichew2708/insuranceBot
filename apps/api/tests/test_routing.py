"""Routing a refusal to the page that does know.

The corpus says what a policy *says*. Where a customer's claim got to, when a
refund lands, whether last week's address change went through — no edition of
it has ever carried those, and answering them from it produced the worst
failures the golden conversation dataset found: "where is my claim now?"
answered with the claim-notification clause, and a customer checking whether
an email was a phishing attempt told to log in and update their details.

These tests hold three things: that such a question is refused, that the
refusal names somewhere to go, and — the half that is easy to lose — that a
question the corpus *can* answer is still answered rather than routed.
"""

from __future__ import annotations

import datetime as dt

import pytest
from api.pipeline import answer_question
from api.route import DESKS, FRAUD_RE, destinations_for, links_for, routed_refusal
from api.settings import Settings
from harness.intent import OUT_OF_CORPUS, Intent, classify, classify_topic

from conftest import make_session
from okf import DESTINATIONS, Bundle, Desk, desk_url, landing_for, renews_online


def ask(bundle: Bundle, settings: Settings, question: str, **kw: object):  # type: ignore[no-untyped-def]
    return answer_question(bundle, question, make_session(**kw), settings)  # type: ignore[arg-type]


# --- the registry ----------------------------------------------------------


def test_every_destination_is_an_absolute_etiqa_url() -> None:
    """A destination is an instruction. One that is relative, or on a host we
    do not own, is an instruction to go somewhere we did not mean."""
    for desk, dest in DESTINATIONS.items():
        assert dest.desk is desk
        assert dest.url.startswith("https://www.etiqa.com.sg/"), dest
        assert dest.provenance.startswith(("crawl ", "owner ")), dest


def test_crawl_provenance_is_actually_in_the_crawl() -> None:
    """An entry claiming the corpus as evidence must have it.

    The two `/LoginPortal/#/` routes claim `owner` instead, and cannot claim
    anything else: they are client-side fragments behind a login and no
    crawler resolves them.
    """
    import json
    from pathlib import Path

    manifest = Path(__file__).resolve().parents[3] / "okf-real/raw/web/crawl-manifest.json"
    if not manifest.exists():  # pragma: no cover - the seed bundle has no crawl
        pytest.skip("no crawl manifest in this checkout")
    crawled = {str(p.get("url", "")).rstrip("/") for p in json.loads(manifest.read_text())["pages"]}
    for dest in DESTINATIONS.values():
        if dest.provenance.startswith("crawl "):
            assert dest.url.rstrip("/") in crawled, f"{dest.url} claims the crawl but is not in it"
        else:
            assert "/LoginPortal/" in dest.url, f"{dest.url} claims `owner` but is crawlable"


def test_the_sentence_carries_the_url_and_the_label() -> None:
    dest = DESTINATIONS[Desk.contact]
    assert dest.url in dest.sentence
    assert dest.label in dest.sentence


# --- classification --------------------------------------------------------


@pytest.mark.parametrize(
    ("question", "intent"),
    [
        ("where is my claim now?", Intent.claim_status),
        ("what is my claim status", Intent.claim_status),
        ("how long will it take to pay out?", Intent.claim_status),
        ("is my claim approved yet?", Intent.claim_status),
        ("when will the refund reach me?", Intent.payment),
        ("can I pay monthly?", Intent.payment),
        ("can I pay for it with MediSave?", Intent.payment),
        ("I moved house last week", Intent.servicing),
        ("can I change my beneficiary?", Intent.servicing),
        ("I forgot my password", Intent.account),
        ("let me speak to someone", Intent.contact),
        ("I want to complain", Intent.contact),
        ("is this email really from you?", Intent.contact),
        ("I got an email asking me to confirm my policy details", Intent.contact),
        # The pivot. Asked cold it names nothing; asked on turn three it used
        # to be answered from whatever product page was in hand.
        ("how much is it?", Intent.price),
        ("just the price then", Intent.price),
    ],
)
def test_the_question_is_classified_as_the_thing_it_is(question: str, intent: Intent) -> None:
    assert classify(question) is intent


@pytest.mark.parametrize(
    ("question", "intent"),
    [
        # Every one of these is answerable from a policy document, and routing
        # it would trade an answer for a link.
        ("I had to call off my trip at the last minute. What do I get back on travel?", Intent.unknown),
        ("how much can I claim for a lost bag?", Intent.limit),
        ("what is the excess on private car", Intent.limit),
        ("how do I make a claim", Intent.claim),
        ("what is not covered", Intent.exclusion),
        ("how do I buy it", Intent.application),
        ("who underwrites this", Intent.entity),
        ("how much does travel insurance cost a year", Intent.price),
        # A bare "how much?" is the one that has to stay out of `price`. On
        # turn three of "does Corporate Travel pay for an 8-hour delay?" it
        # asks for the benefit, and an early cut of the bare-price pattern
        # read it as a price question and turned three answered limit
        # questions into refusals. The object is the whole difference.
        ("how much?", Intent.unknown),
        ("how much", Intent.unknown),
        # "How do I pay for X" is a question about buying a plan and the
        # product page answers it. Only a *schedule* or a *method* is an
        # account action. An early cut routed this and traded a good answer
        # for a link.
        ("How do I pay for Tiq Travel Insurance?", Intent.unknown),
    ],
)
def test_a_question_the_corpus_answers_is_not_routed_away(question: str, intent: Intent) -> None:
    assert classify(question) is intent
    assert intent not in OUT_OF_CORPUS or intent is Intent.price


# --- the destinations a question resolves to -------------------------------


def test_a_claim_status_question_goes_to_claims_first() -> None:
    dests = destinations_for(Intent.claim_status, None)
    assert [d.desk for d in dests] == [Desk.claims, Desk.portal]


def test_a_fraud_report_goes_to_a_person_and_nowhere_else() -> None:
    """Never the portal. Telling someone who may have just been phished to go
    and log in somewhere is the answer this whole module exists to stop."""
    question = "I got an email asking me to confirm my policy details"
    assert FRAUD_RE.search(question)
    assert [d.desk for d in destinations_for(Intent.contact, None, question)] == [Desk.contact]
    said = routed_refusal(Intent.contact, None, question)
    assert desk_url(Desk.portal) not in said
    assert "don't act on that message" in said


def test_a_complaint_is_not_treated_as_fraud() -> None:
    said = routed_refusal(Intent.contact, None, "I want to complain")
    assert "don't act on that message" not in said
    assert desk_url(Desk.contact) in said


def test_general_insurance_renews_online_and_a_savings_plan_does_not(bundle: Bundle) -> None:
    """Sending an endowment holder to the general-insurance renewal route
    would be a wrong instruction, not a vague one."""
    motor = bundle.get("product/general/travel") or next(
        p for p in bundle.pages.values() if p.id.startswith("product/")
    )
    assert renews_online(motor) is (motor.frontmatter.line_of_business in {"general", "motor"})
    assert renews_online(None) is False


def test_the_prose_and_the_link_list_never_disagree() -> None:
    """A client renders the buttons and a customer reads the sentence. If the
    two are built from different tables they drift, and the drift is silent."""
    for intent in [*OUT_OF_CORPUS, Intent.offer, Intent.renewal, Intent.claim]:
        said = routed_refusal(intent, None, "")
        for link in links_for(intent, None, ""):
            assert link.url in said, f"{intent.value}: {link.url} is offered but never said"


def test_every_out_of_corpus_intent_has_somewhere_to_send_the_customer() -> None:
    for intent in OUT_OF_CORPUS:
        assert DESKS.get(intent), f"{intent.value} is refused with nowhere to go"


# --- end to end ------------------------------------------------------------


def test_a_backend_state_question_is_refused_with_a_destination(bundle: Bundle, settings: Settings) -> None:
    env, _ = ask(bundle, settings, "where is my claim now?")
    assert env.answer.handoff
    assert env.answer.destinations
    assert desk_url(Desk.claims) in env.answer.answer
    # Refused before retrieval, so it cost no pages: the point of deciding
    # this on the intent rather than on what came back.
    assert not env.answer.claims


def test_the_routed_turn_still_goes_through_the_gates(bundle: Bundle, settings: Settings) -> None:
    """A short-circuit that skipped verification would look, on the trace,
    exactly like one that passed it."""
    env, _ = ask(bundle, settings, "I forgot my password")
    assert env.gates
    assert desk_url(Desk.portal) in env.answer.answer


def test_a_phishing_report_is_never_told_to_log_in(bundle: Bundle, settings: Settings) -> None:
    env, _ = ask(bundle, settings, "I got an email asking me to confirm my policy details")
    said = env.answer.answer.lower()
    assert "log in" not in said.replace("log in through a link", "")
    assert desk_url(Desk.contact) in env.answer.answer


def test_a_coverage_question_is_still_answered_from_the_corpus(bundle: Bundle, settings: Settings) -> None:
    """The regression that matters most: routing must not eat the questions
    the corpus is for."""
    env, _ = ask(bundle, settings, "What is the overseas medical expenses limit?")
    assert env.delivered
    assert not env.answer.handoff
    assert env.answer.figures


def test_the_product_page_is_offered_from_the_corpus_not_a_table(bundle: Bundle) -> None:
    """37 product URLs copied into a registry would rot. The binding is
    compiled from the site and the channel gate already trusts it."""
    product = bundle.get("product/general/travel")
    assert product is not None
    link = landing_for(product)
    assert link is None or link.startswith("http")
    assert landing_for(None) is None


def test_todays_date_does_not_change_a_routed_answer(bundle: Bundle, settings: Settings) -> None:
    """The registry is not effective-dated, and a routed refusal asserts
    nothing that could go stale between two runs."""
    first, _ = ask(bundle, settings, "let me speak to someone", today=dt.date(2026, 1, 1))
    later, _ = ask(bundle, settings, "let me speak to someone", today=dt.date(2027, 1, 1))
    assert first.answer.answer == later.answer.answer


def test_the_object_is_what_makes_how_much_a_price_question() -> None:
    """ "How much is it" is about the plan. "How much" alone is about whatever
    was last discussed, and on this corpus that is a benefit."""
    assert classify("how much is it?") is Intent.price
    assert classify("just the price then") is Intent.price
    assert classify("how much?") is not Intent.price
    assert classify("how much can I claim?") is Intent.limit


def test_paying_for_a_plan_is_not_paying_a_premium() -> None:
    """One is how you buy it — the product page answers that. The other is an
    instruction to a ledger this system cannot see."""
    assert classify("How do I pay for Tiq Travel Insurance?") is not Intent.payment
    for account_action in (
        "how do I pay my premium",
        "can I pay monthly?",
        "can I pay for it with MediSave?",
        "what payment methods do you take?",
        "when will the refund reach me?",
    ):
        assert classify(account_action) is Intent.payment, account_action


@pytest.mark.parametrize(
    ("question", "intent"),
    [
        # The account-state forms customers actually type, taken from the
        # golden dataset's own handoff cases rather than invented here.
        ("Can I pay by credit card?", Intent.payment),
        ("Can I pay my overdue premiums?", Intent.payment),
        ("My payment failed", Intent.payment),
        ("My card was declined", Intent.payment),
        ("Can I change how often I pay my premium?", Intent.payment),
        ("When is my next premium payment due?", Intent.payment),
        ("Can I get a receipt for my premium payment?", Intent.payment),
        ("How do I get a refund?", Intent.payment),
        ("Has my claim payment been processed?", Intent.claim_status),
        ("When will my claim payment reach my bank account?", Intent.claim_status),
        ("Someone called me claiming to be your agent and asked for payment", Intent.contact),
    ],
)
def test_the_account_state_questions_route(question: str, intent: Intent) -> None:
    assert classify(question) is intent
    assert intent in OUT_OF_CORPUS


@pytest.mark.parametrize(
    "question",
    [
        # ...and the payment questions a policy document *does* answer. Each
        # of these is a `product_fact` case in the golden dataset, and routing
        # any of them would trade a real answer for a link.
        "How do I pay for Tiq Travel Insurance?",
        "Is there a grace period for paying premiums on Term Life Insurance?",
        "What happens to Term Life Insurance if I stop paying premiums?",
        "Why do I have to pay an excess on Private Car Insurance?",
        "Do I have to pay the hospital first and claim later under Tiq Personal Accident?",
        "What benefits does Cancer Insurance pay?",
        "What is the maximum Cancer Insurance will pay out?",
    ],
)
def test_a_payment_question_the_corpus_answers_is_never_routed(question: str) -> None:
    assert classify(question) not in OUT_OF_CORPUS, question


# --- headings are topics, not questions ------------------------------------


@pytest.mark.parametrize(
    ("heading", "topic"),
    [
        # The six FAQ headings in the real corpus whose intent the out-of-corpus
        # split moved away from a real topic. Each is corpus: the paragraph
        # under it is the answer, so it cannot be "out of corpus", and the
        # composer matching it against a customer's question must read it as
        # what it is about.
        ("When will GIRO deductions be made for the renewal premium?", Intent.renewal),
        ("When will Giro deductions be made for the renewal premium ?", Intent.renewal),
        ("Am I able to auto-renew my policy via GIRO?", Intent.renewal),
        ("What are my premium payment options?", Intent.price),
        ("What is covered under Cyber Fraud?", Intent.coverage),
    ],
)
def test_a_heading_is_read_as_its_topic(heading: str, topic: Intent) -> None:
    assert classify_topic(heading) is topic
    assert classify_topic(heading) not in OUT_OF_CORPUS


def test_topic_reading_never_yields_an_out_of_corpus_intent() -> None:
    """By construction — and the property `faq_pick` relies on. The three
    cancer insurance turns this guards were lost because a renewal heading,
    read as a question, became `payment`, the same-intent count fell from two
    to one, and a low-overlap FAQ entry led the answer straight into the
    entitlement gate."""
    for question in (
        "where is my claim now?",
        "can I pay monthly?",
        "I forgot my password",
        "let me speak to someone",
        "I moved house last week",
        "When will GIRO deductions be made for the renewal premium?",
    ):
        assert classify_topic(question) not in OUT_OF_CORPUS, question


def test_the_question_reading_and_the_topic_reading_differ_only_off_corpus() -> None:
    """Where `classify` gives an in-corpus intent, the two agree; the topic
    reading is `classify` with the out-of-corpus patterns skipped and nothing
    else."""
    for text in (
        "how do I renew it?",
        "what is not covered",
        "how much can I claim for a lost bag?",
        "who underwrites this",
        "what does it cover?",
    ):
        assert classify(text) is classify_topic(text), text


# --- fraud is something reported, never something covered -------------------


@pytest.mark.parametrize(
    "question",
    [
        "What is covered under Cyber Fraud?",
        "Does Personal Cyber Insurance cover fraud?",
        "does it cover credit card fraud?",
        "am I covered for online fraud?",
    ],
)
def test_a_cover_question_about_fraud_is_not_routed(question: str) -> None:
    """A bare `fraud` sent these to the contact page. They are coverage
    questions on the cyber product, and the corpus answers them."""
    assert classify(question) not in OUT_OF_CORPUS
    assert not FRAUD_RE.search(question)


@pytest.mark.parametrize(
    "question",
    [
        "I want to report suspected insurance fraud",
        "is this email a fraud?",
        "someone made a fraudulent claim on my card",
        "is this a scam?",
        "I got a phishing email",
    ],
)
def test_a_fraud_report_is_routed_to_a_person(question: str) -> None:
    assert classify(question) is Intent.contact
    assert FRAUD_RE.search(question) or "report" in question
