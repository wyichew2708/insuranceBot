"""Turn a refusal into a direction.

Two tiers, and the difference between them is whether the corpus ever had a
chance.

**Tier 1 — routed before retrieval.** `harness.intent.OUT_OF_CORPUS` names the
five intents no policy document can settle: where a claim got to, what a
refund will be, whether an address change went through, a password, a person.
Retrieval cannot help and costs a page budget and a model call to prove it, so
these turns never enter it. They are answered deterministically, from the
registry, and marked `handoff` — which is what they were always supposed to be.

**Tier 2 — routed after the gates.** Everything else is a real question about a
product, and the corpus may well answer it. When it does, nothing here runs.
When the answerability gate refuses, `destinations_for` supplies the page that
does know, and the existing shortfall sentence gains a place to go instead of
ending on "a colleague can confirm it".

The order in a returned list is the order the answer offers them: most
specific first. A customer sent to two places will use the first one.
"""

from __future__ import annotations

import re

from harness.contracts import Link
from harness.intent import Intent

from okf import DESTINATIONS, Desk, Destination, Page, landing_for, renews_online

#: A message the customer did not expect and is checking on. Answered from the
#: corpus once as "Please log in to TiqConnect to update your details" — a
#: faithful quotation of a real page, and precisely the instruction a phishing
#: attempt wants a customer to follow. Nothing about this may come from
#: retrieval.
FRAUD_RE = re.compile(
    r"\bphish(?:ing)?\b|\bscam(?:mer|med)?\b|\bunauthorised\b"
    r"|\bfraud(?:ulent)?\s+(?:e-?mail|sms|message|text|call(?:er)?|website|link|transaction|charge|claim)\b"
    r"|\b(?:report(?:ing)?|suspect(?:ed)?|victim of|targeted by)\b[\w\s]{0,30}\bfraud\b"
    r"|\b(?:is|was) (?:this|that|it)[\w\s]{0,12}\bfraud\b"
    r"|\bsomeone (?:used|accessed|hacked)\b|\bdid ?n.t request\b|\bnever requested\b"
    r"|\b(?:is|was) (?:this|that|it) (?:email|sms|message|text|call|really|actually)"
    r"|\breally from (?:you|etiqa|tiq)\b|\botp\b"
    r"|\bi (?:got|received|have had|had) (?:an?|this|some) "
    r"(?:e-?mail|sms|message|text|call|whatsapp|letter)\b",
    re.I,
)

#: What each out-of-corpus intent leads with, before the destinations. Says
#: what this system cannot do and why — a customer told "I'm passing you to a
#: colleague" learns nothing; a customer told "I can't see your claim from
#: here" knows not to rephrase and try again.
OPENERS: dict[Intent, str] = {
    Intent.claim_status: (
        "I can't see your claim from here — I answer from our published policy "
        "documents, and they say what a claim needs, never where yours has got to."
    ),
    Intent.servicing: (
        "I can't make changes to a policy from here, and I can't confirm one that was already made."
    ),
    Intent.payment: (
        "I can't see your payments or set one up from here — premiums, refunds and "
        "payment arrangements live on your policy record rather than in the product "
        "documents I answer from."
    ),
    Intent.account: "I can't get into your account or reset anything from here.",
    Intent.contact: "Of course — that one needs a person.",
}

#: The safety line for a message the customer is checking the provenance of.
#: Replaces the opener rather than joining it: this is the whole answer.
FRAUD_OPENER = (
    "Please don't act on that message, log in through a link in it, or reply to it "
    "with any of your details. I can't verify a message from here — check it with "
    "us directly first."
)

#: intent → desks, most specific first. Only for the intents that reach a
#: desk. An intent absent from this map routes to the product's own page and
#: nothing else, which is what `destinations_for` falls back to.
DESKS: dict[Intent, tuple[Desk, ...]] = {
    Intent.claim_status: (Desk.claims, Desk.portal),
    Intent.servicing: (Desk.claims, Desk.portal),
    Intent.payment: (Desk.portal, Desk.contact),
    Intent.account: (Desk.portal,),
    Intent.contact: (Desk.contact,),
    # Tier 2. These are answerable from the corpus and usually are; the desk
    # is offered only where the gate has already refused.
    Intent.offer: (Desk.promotions,),
    Intent.claim: (Desk.claims,),
    Intent.document: (Desk.portal,),
    Intent.price: (),
    Intent.renewal: (),
}


def destinations_for(intent: Intent, product: Page | None, question: str = "") -> list[Destination]:
    """Where a customer asking this should be sent, best first.

    `renewal` is resolved rather than tabled: a general-insurance policy
    renews through the online renewal route and a life or savings policy does
    not, so sending every renewal question to the same page would be wrong for
    a third of the catalogue (`okf.destinations.renews_online`).
    """
    if intent is Intent.contact and FRAUD_RE.search(question):
        # A fraud report goes to a person and nowhere else. The portal is a
        # login page, and telling someone who may have just been phished to go
        # and log in somewhere is the answer we are here to stop giving.
        return [DESTINATIONS[Desk.contact]]
    desks = list(DESKS.get(intent, ()))
    if intent is Intent.renewal:
        desks = [Desk.renewal] if renews_online(product) else [Desk.contact]
    out = [DESTINATIONS[d] for d in dict.fromkeys(desks)]
    return out


def product_link(product: Page | None) -> str | None:
    """The product's own page, if the corpus carries one for it."""
    return landing_for(product)


def routed_refusal(
    intent: Intent, product: Page | None, question: str = "", opener: str | None = None
) -> str:
    """The whole reply for a tier-1 turn, or the tail for a tier-2 one.

    `opener` is the caller's own first sentence — the shortfall text, on the
    tier-2 path. Where it is None the intent's own opener is used, which is
    the tier-1 case.
    """
    if opener is None:
        opener = (
            FRAUD_OPENER
            if intent is Intent.contact and FRAUD_RE.search(question)
            else OPENERS.get(intent, "")
        )
    parts = [opener.strip()] if opener else []
    parts += [d.sentence for d in destinations_for(intent, product, question)]
    if (link := product_link(product)) is not None and intent not in (
        Intent.account,
        Intent.contact,
    ):
        # The product's own page, last: it is the least specific of the
        # answers, and on a claim-status question it is not an answer at all —
        # but a customer who came in about a product usually wants it to hand.
        parts.append(f"The plan's own page has the published detail: {link}")
    return " ".join(p.rstrip() for p in parts if p)


def links_for(intent: Intent, product: Page | None, question: str = "") -> list[Link]:
    """The same destinations, structurally, for a client to render as buttons.

    The prose and this list are built from one table and stay in step by
    construction: a destination that appears in the sentence appears here, in
    the same order, and nothing appears in one and not the other.
    """
    links = [
        Link(label=d.label, url=d.url, desk=d.desk.value) for d in destinations_for(intent, product, question)
    ]
    if (link := product_link(product)) is not None and intent not in (Intent.account, Intent.contact):
        links.append(
            Link(label=product.frontmatter.title if product else "Product page", url=link, desk="product")
        )
    return links
