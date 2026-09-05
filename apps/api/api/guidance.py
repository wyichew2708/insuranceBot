"""How to get the answer this system cannot give — as steps, not a shrug.

A refusal that ends in "a colleague can confirm it" leaves the customer where
they started. The product owner's rule (2026-09-05): where the corpus cannot
answer, the reply tells the customer how to get the real answer themselves —
the steps, in order, and the page each one happens on.

Everything here is a table. The steps are written once; the addresses come
from `okf.destinations`, the registry the owner supplied; the product's own
page comes from its channel binding in the corpus; its documents come from the
raw files the catalogue tags to it. Nothing comes from retrieval and nothing
comes from a model, so the steps cannot be steered by a page or a prompt.

The reply names the product, and that name is a claim bound to the product's
page — reference-integrity has something to check. It asserts nothing about
cover, so the coverage gates have nothing to check. And it carries no digits:
the numeric-binding gate reads a bare number as a figure, and "step 1" is not
a figure, so the steps are bulleted and the prose is written without numbers.
Only registry addresses and a product address free of digit runs appear in
the prose; a document address, which carries its upload date, is offered as a
structured link and not spelt out.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from harness import Claim, GroundedAnswer
from harness.contracts import Link
from harness.intent import Intent

from okf import DESTINATIONS, Bundle, Desk, Page, PageType, landing_for, raw_product_index, renews_online


class Topic(str, Enum):
    quote = "quote"
    refund = "refund"
    payment = "payment"
    policy_record = "policy_record"
    application = "application"
    claims = "claims"
    claim_status = "claim_status"
    cancellation = "cancellation"
    renewal = "renewal"
    documents = "documents"
    eligibility = "eligibility"
    apply = "apply"
    contact = "contact"
    generic = "generic"


@dataclass(frozen=True)
class Guide:
    opener: str
    steps: tuple[str, ...]
    #: Registry desks the steps use, for the structured link list.
    desks: tuple[Desk, ...] = ()


_URL = {d.value: DESTINATIONS[d].url for d in Desk}
_PLAN = "the plan's page{plan}"

GUIDES: dict[Topic, Guide] = {
    Topic.quote: Guide(
        opener=(
            "I don't have premiums for {product} — they aren't published in the product "
            "documents I answer from, and the price depends on your details. "
            "Here's how to get your price:"
        ),
        steps=(
            f"Open {_PLAN} and choose Get a quote.",
            "Enter the details it asks for — who is covered, the dates or period, and the plan tier.",
            "The quote shows the premium for each tier before anything is bought, and you can "
            "change the options to compare.",
            f"For a life or savings plan, an adviser can prepare the quote with you: {_URL['contact']}",
        ),
        desks=(Desk.contact,),
    ),
    Topic.refund: Guide(
        opener=(
            "I can't see what you'd get back — a refund is worked out on your own policy record, "
            "which I don't have access to. Here's how to find out:"
        ),
        steps=(
            f"Log in to the customer portal: {_URL['portal']}",
            "Open the policy and choose the cancellation or servicing option — the refund it shows "
            "is calculated for your policy and your dates.",
            "The rule it applies (free-look, pro-rata or short-period) is in the policy wording, "
            f"on {_PLAN}.",
            f"To have it confirmed by a person, contact us with your policy number: {_URL['contact']}",
        ),
        desks=(Desk.portal, Desk.contact),
    ),
    Topic.payment: Guide(
        opener=(
            "I can't see your payments or set one up from here — premiums, charges and payment "
            "arrangements live on your policy record rather than in the product documents "
            "I answer from. Here's where they are:"
        ),
        steps=(
            f"Log in to the customer portal to see the premium due, the payment history and the "
            f"payment methods on your policy: {_URL['portal']}",
            "A charge that looks wrong is corrected from the record — contact us with the policy "
            f"number and the date of the charge: {_URL['contact']}",
        ),
        desks=(Desk.portal, Desk.contact),
    ),
    Topic.policy_record: Guide(
        opener=(
            "I can't see your policy record from here — the policy number, its dates and status, "
            "the people on it and your own documents live in your account rather than in the "
            "product pages I answer from. Here's how to see them:"
        ),
        steps=(
            f"Log in to the customer portal: {_URL['portal']}",
            "Open My Policies — each policy shows its number, status, start and expiry dates, "
            "the people covered and its documents.",
            "If you can't log in, contact us and a colleague will verify you and read it back: "
            f"{_URL['contact']}",
        ),
        desks=(Desk.portal, Desk.contact),
    ),
    Topic.application: Guide(
        opener="I can't see an application in progress from here. Here's how to pick it up:",
        steps=(
            f"Log in to the customer portal — a saved or submitted application, and its status, "
            f"are shown under your account: {_URL['portal']}",
            "To change something you entered, or if no confirmation arrived, contact us with the "
            f"name and email used on the application: {_URL['contact']}",
        ),
        desks=(Desk.portal, Desk.contact),
    ),
    Topic.claims: Guide(
        opener=(
            "I don't have the claim steps for {product} in the pages I answer from. "
            "Here's how to make the claim:"
        ),
        steps=(
            f"Go to Claims and services and choose the product: {_URL['claims']}",
            "Submit the claim form online with the supporting documents it lists — keep the policy "
            "number and the date of the incident to hand.",
            "You'll get an acknowledgement with a reference, and can track it in the customer portal: "
            f"{_URL['portal']}",
            f"The full claim conditions are in the policy wording, on {_PLAN}.",
        ),
        desks=(Desk.claims, Desk.portal),
    ),
    Topic.claim_status: Guide(
        opener=(
            "I can't see your claim from here — I answer from our published policy documents, "
            "and they say what a claim needs, never where yours has got to. Here's how to check it:"
        ),
        steps=(
            f"Track the claim in the customer portal — it shows the stage, anything outstanding "
            f"and the outcome: {_URL['portal']}",
            f"Claim tracking and forms are also here: {_URL['claims']}",
            "To add a document, amend a claim or appeal a decision, contact us with the claim "
            f"reference: {_URL['contact']}",
        ),
        desks=(Desk.portal, Desk.claims, Desk.contact),
    ),
    Topic.cancellation: Guide(
        opener="Here's how to cancel {product}, and where its terms are:",
        steps=(
            f"Log in to the customer portal and open the policy — the servicing options include "
            f"cancellation, and it shows any refund due: {_URL['portal']}",
            f"Or contact us with the policy number and the date you want the cover to end: {_URL['contact']}",
            f"The free-look, refund and notice terms are in the policy wording, on {_PLAN}.",
        ),
        desks=(Desk.portal, Desk.contact),
    ),
    Topic.renewal: Guide(
        opener="Here's how to renew {product}:",
        steps=(
            f"Renew online here, with the policy number to hand: {_URL['renewal']}",
            "You can review the cover and pay in the same place.",
            f"If the policy has already expired, contact us and a colleague will check whether it "
            f"can still be renewed: {_URL['contact']}",
        ),
        desks=(Desk.renewal, Desk.contact),
    ),
    Topic.documents: Guide(
        opener="I can't send documents, but here's where each one is:",
        steps=(
            f"The policy wording and product summary for {{product}} are published on {_PLAN}{{docs}}",
            "Your own policy schedule and certificate are in the customer portal, under the policy: "
            f"{_URL['portal']}",
            f"For a copy sent to you, contact us with your policy number: {_URL['contact']}",
        ),
        desks=(Desk.portal, Desk.contact),
    ),
    Topic.eligibility: Guide(
        opener=(
            "The eligibility rules for {product} aren't in the pages I answer from. "
            "Here's how to check whether you can buy it:"
        ),
        steps=(
            f"{_PLAN[0].upper()}{_PLAN[1:]} lists who can apply — age, residency and any other conditions.",
            "Start a quote: the application checks your eligibility from the details you enter, "
            "before anything is bought.",
            f"If your situation is unusual, contact us and a colleague can confirm it: {_URL['contact']}",
        ),
        desks=(Desk.contact,),
    ),
    Topic.apply: Guide(
        opener="Here's how to buy {product}:",
        steps=(
            f"Open {_PLAN} and choose Get a quote.",
            "Enter your details, pick the plan tier and pay online.",
            f"Your policy documents arrive by email and sit in the customer portal: {_URL['portal']}",
        ),
        desks=(Desk.portal,),
    ),
    Topic.contact: Guide(
        opener="Of course — that one needs a person.",
        steps=(f"Contact us — the page has the hotline, email and opening hours: {_URL['contact']}",),
        desks=(Desk.contact,),
    ),
    Topic.generic: Guide(
        opener="The pages I answer from don't settle that for {product}. Here's where the answer is:",
        steps=(
            f"{_PLAN[0].upper()}{_PLAN[1:]} has the published detail and the policy wording.",
            f"For your own policy, log in to the customer portal: {_URL['portal']}",
            f"A colleague can confirm anything the pages leave open: {_URL['contact']}",
        ),
        desks=(Desk.portal, Desk.contact),
    ),
}

#: Life and savings plans do not renew; premiums continue on the schedule in
#: the policy. Said instead of sending the customer to a renewal page that
#: will not know the policy.
_NO_RENEWAL = Guide(
    opener=(
        "{product} doesn't renew — it's a plan whose premiums continue on the schedule in your policy. "
        "Here's where to see them:"
    ),
    steps=(
        f"Log in to the customer portal to see the premium due and the payment dates: {_URL['portal']}",
        f"To change the payment method or frequency, contact us with your policy number: {_URL['contact']}",
    ),
    desks=(Desk.portal, Desk.contact),
)

_CANCEL_RE = re.compile(r"\b(?:cancel|cancell?ation|terminate|free.look|cooling|surrender)\w*", re.I)
_REFUND_RE = re.compile(
    r"\brefund|\b(?:get|getting|receive|receiving|have|having)\b[\w\s]{0,16}\bback\b", re.I
)
_APPLICATION_RE = re.compile(r"\bapplication\b|\bapplied\b|\bconfirmation after\b", re.I)
_DIGIT_RUN_RE = re.compile(r"\d{2,}")


def topic_for(intent: Intent, question: str) -> Topic:
    """Which guide answers a question of this intent."""
    if intent is Intent.price:
        return Topic.quote
    if intent is Intent.payment:
        return Topic.refund if _REFUND_RE.search(question) else Topic.payment
    if intent is Intent.account:
        return Topic.application if _APPLICATION_RE.search(question) else Topic.policy_record
    if intent is Intent.claim_status:
        return Topic.application if _APPLICATION_RE.search(question) else Topic.claim_status
    if intent is Intent.servicing:
        return Topic.policy_record
    if intent is Intent.contact:
        return Topic.contact
    if intent is Intent.claim:
        return Topic.claims
    if intent is Intent.renewal:
        return Topic.cancellation if _CANCEL_RE.search(question) else Topic.renewal
    if intent is Intent.document:
        return Topic.documents
    if intent is Intent.eligibility:
        return Topic.eligibility
    if intent is Intent.application:
        return Topic.apply
    return Topic.generic


def root_page(bundle: Bundle, page: Page | None) -> Page | None:
    """The product's own page for any page of the product."""
    if page is None:
        return None
    if page.id.count("/") == 2 and page.frontmatter.type == PageType.product:
        return page
    key = bundle.product_key(page)
    for candidate in bundle.pages.values():
        if (
            candidate.id.count("/") == 2
            and candidate.frontmatter.type == PageType.product
            and bundle.product_key(candidate) == key
        ):
            return candidate
    return None


def _read_source_url(path: Path) -> str:
    """The `source_url` from a raw file's frontmatter, or ''."""
    try:
        with path.open(encoding="utf-8") as handle:
            head = handle.read(2000)
    except OSError:
        return ""
    match = re.search(r'^source_url:\s*"?([^"\n]+)"?\s*$', head, re.M)
    return match.group(1).strip() if match else ""


def _label(rel: str) -> str:
    stem = rel.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    return " ".join(w.capitalize() for w in stem.replace("_", "-").split("-"))


def document_links(bundle: Bundle, raw_root: Path | None, product: Page | None) -> list[Link]:
    """The product's own published documents — wordings and product summaries —
    as links to where the insurer publishes them.

    Read from the raw files the catalogue tags to the product, which is the
    same tagging retrieval is scoped by. A document that tagging does not
    attribute to this product is not offered as this product's.
    """
    root = root_page(bundle, product)
    if root is None or raw_root is None:
        return []
    key = bundle.product_key(root)
    out: list[Link] = []
    for rel, tag in sorted(raw_product_index(bundle).items()):
        published = rel.startswith("raw/wordings/") or rel.startswith("raw/product-summaries/")
        if tag != key or not published:
            continue
        url = _read_source_url(raw_root / rel.removeprefix("raw/"))
        if url and all(link.url != url for link in out):
            out.append(Link(label=_label(rel), url=url, desk="document"))
    return out


def _name(page: Page | None) -> str:
    return page.frontmatter.title.split(" — ")[0] if page is not None else "this plan"


def guidance(
    bundle: Bundle,
    raw_root: Path | None,
    intent: Intent,
    product: Page | None,
    question: str = "",
    opener: str | None = None,
) -> GroundedAnswer:
    """The step-by-step reply for a question this system cannot answer itself.

    `opener` replaces the guide's own first sentence where the caller has
    something more specific to say first — the fraud safety line, or the
    answerability shortfall.
    """
    topic = topic_for(intent, question)
    root = root_page(bundle, product)
    guide = GUIDES[topic]
    if topic is Topic.renewal and root is not None and not renews_online(root):
        guide = _NO_RENEWAL
    plan_url = landing_for(root)
    # A product address with a digit run would read as a figure to the
    # numeric-binding gate; it is offered as a link instead of spelt out.
    plan = f": {plan_url}" if plan_url and not _DIGIT_RUN_RE.search(plan_url) else ""
    docs = document_links(bundle, raw_root, root) if topic is Topic.documents else []
    name = _name(root)
    fields = {
        "product": name,
        "plan": plan,
        "docs": " — the documents themselves are linked below." if docs else ".",
    }
    lead = (opener or guide.opener).format(**fields).strip()
    if root is None:
        lead = lead.replace("for this plan ", "").replace("This Plan", "The plan")
    steps = [step.format(**fields) for step in guide.steps]
    text = lead + "\n" + "\n".join(f"- {step}" for step in steps)

    links = [Link(label=DESTINATIONS[d].label, url=DESTINATIONS[d].url, desk=d.value) for d in guide.desks]
    if plan_url and root is not None:
        links.append(Link(label=name, url=plan_url, desk="product"))
    links.extend(docs)
    claims = [Claim(text=name, source_id=root.id, locator=root.id)] if root is not None else []
    return GroundedAnswer(
        answer=text,
        claims=claims,
        # A handoff with steps: this system did not answer, and says where the
        # answer is. The flag is what the contract and the evaluation read.
        handoff=True,
        guidance=True,
        destinations=links,
        confidence=1.0,
    )
