"""Where to send a customer whose question no corpus can answer.

The wiki knows what a policy *says*. It does not know what *your* policy is
doing — where your claim is, when your refund lands, whether last week's
address change went through. Measured on the golden conversation dataset,
that distinction is the single largest failure class: 237 of 379 failing
turns were questions of that kind, answered anyway from whichever product
page retrieval had in hand. "Where is my claim now?" came back with the
claim-notification clause; "when will the refund reach me?" with the terms of
a 2024 promotion.

Refusing those is right and, on its own, useless: "I'm passing you to a
colleague" is a dead end the customer cannot act on. What makes a refusal
useful is a **destination** — the page that does know. This module is the
registry of them.

Destinations are a committed table, never model-generated and never inferred
from a search result, for the same reason the channel registry is
(`channels.py`): a URL handed to a customer is an instruction, and an
instruction assembled at runtime out of retrieved text is an instruction an
attacker can write. Each entry records where it came from, because two of
them cannot be corroborated any other way:

* **crawl** — the address is in `raw/web/` with a 200 at the crawl date, so
  the compiled corpus is evidence for it.
* **owner** — supplied directly by the product owner. The portal routes are
  client-side fragments behind a login (`/LoginPortal/#/...`), which no
  crawler resolves and no crawl can therefore confirm.

The product's own page is deliberately *not* here. It is already in the
corpus, on the product page's `channel/direct` binding, per product and
already blessed by the channel-coherence gate — `landing_for` reads it there
rather than duplicating 37 URLs into this file, where they would rot.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from okf.channels import Channel
from okf.page import Page


class Desk(str, Enum):
    """The back-office function a question belongs to."""

    claims = "claims"  # claims and post-sale servicing
    renewal = "renewal"  # general-insurance online renewal
    portal = "portal"  # the customer's own policy account
    promotions = "promotions"  # what is on offer right now
    contact = "contact"  # a human, a complaint, anything urgent


@dataclass(frozen=True)
class Destination:
    """One place a customer can be sent, and the sentence that sends them."""

    desk: Desk
    #: How the answer names it. A customer clicks a name, not a URL.
    label: str
    url: str
    #: What the answer says the customer will find there. One clause, present
    #: tense, no promises about outcome — this system does not know whether
    #: their claim will be paid and must not imply that the page does.
    holds: str
    #: "crawl <date>" or "owner <date>". Read by the test that asserts every
    #: crawl-provenance entry is actually in the crawl manifest.
    provenance: str

    @property
    def sentence(self) -> str:
        return f"{self.holds.rstrip('.')}: {self.label} — {self.url}"


#: Supplied by the product owner on 2026-09-05. Four of the five are also in
#: the 2026-08-25 crawl at status 200; the two `/LoginPortal/` fragments are
#: not, and cannot be — see the module docstring.
DESTINATIONS: dict[Desk, Destination] = {
    Desk.claims: Destination(
        desk=Desk.claims,
        label="Claims and services",
        url="https://www.etiqa.com.sg/claims-and-services/",
        holds="Claim tracking, forms and policy servicing are handled here",
        provenance="crawl 2026-08-25",
    ),
    Desk.renewal: Destination(
        desk=Desk.renewal,
        label="Online renewal",
        url="https://www.etiqa.com.sg/LoginPortal/#/OnlineRenewal",
        holds="General insurance policies renew here",
        provenance="owner 2026-09-05",
    ),
    Desk.portal: Destination(
        desk=Desk.portal,
        label="Customer portal",
        url="https://www.etiqa.com.sg/LoginPortal/#/",
        holds="Your own policy, payments and documents are in your account",
        provenance="owner 2026-09-05",
    ),
    Desk.promotions: Destination(
        desk=Desk.promotions,
        label="Promotions",
        url="https://www.etiqa.com.sg/promotions/",
        holds="Current offers are listed here and change often",
        provenance="crawl 2026-08-25",
    ),
    Desk.contact: Destination(
        desk=Desk.contact,
        label="Contact us",
        url="https://www.etiqa.com.sg/contact-us/",
        holds="A colleague can pick this up",
        provenance="crawl 2026-08-25",
    ),
}

#: Lines of business whose policies renew through the general-insurance online
#: renewal route. Life, protection, savings and investment policies do not
#: renew that way and must never be sent there.
GENERAL_LINES: frozenset[str] = frozenset({"general", "motor", "business", "premier", "health-medical"})

#: Products compiled as `line_of_business: general` that the catalogue groups
#: under Savings & Investments. They are endowment plans: they do not renew,
#: and the online renewal route is wrong for them. Named here rather than
#: fixed here — the mismatch belongs to the compile, and correcting it there
#: makes this set empty rather than making it wrong.
NOT_RENEWABLE: frozenset[str] = frozenset({"cashsaver", "enrich-aspire-vii"})


def desk_url(desk: Desk) -> str:
    return DESTINATIONS[desk].url


def renews_online(product: Page | None) -> bool:
    """Whether this product renews through the online renewal route."""
    if product is None:
        return False
    slug = product.id.rsplit("/", 1)[-1]
    return (product.frontmatter.line_of_business or "") in GENERAL_LINES and slug not in NOT_RENEWABLE


def landing_for(product: Page | None, channel: Channel = Channel.direct) -> str | None:
    """The product's own page, from its channel binding in the corpus.

    Read from the binding rather than from a table in this module: the binding
    is compiled from the site, it is per product, and the channel-coherence
    gate already treats it as this route's own address. A destination taken
    from anywhere else would be a URL the gate has never heard of.
    """
    if product is None:
        return None
    for binding in product.frontmatter.channels:
        if binding.ref == channel.value and binding.landings:
            return binding.landings[0]
    return None
