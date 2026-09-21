"""What the customer might ask next, offered rather than waited for.

A customer who has just read what Tiq Home Insurance covers usually wants
one of a handful of things next — what it does not cover, how to claim, what
it costs or whether there is an offer, how to buy. The suggestions are built
from the Ask (what was just asked) and from what the corpus actually holds
for the product: an exclusions page, a claims journey, a promotion, a
benefit table. A topic the corpus cannot answer is never offered, so a
suggestion tapped is a question answered.

Deterministic on purpose. The chips are phrased in the product's own name so
the next turn names its product and needs no model to resolve it.
"""

from __future__ import annotations

import datetime as dt

from harness.ask import Ask
from harness.intent import Intent

from okf import Bundle, Page, Status

#: How many chips to offer.
MAX_SUGGESTIONS = 4

#: Per intent, the topics worth offering next, in order. `overview` is the
#: introduction a bare product name gets.
NEXT_TOPICS: dict[str, tuple[str, ...]] = {
    "overview": ("exclusion", "limit", "eligibility", "application", "claim"),
    Intent.coverage.value: ("exclusion", "limit", "claim", "application"),
    Intent.exclusion.value: ("coverage", "claim", "application", "offer"),
    Intent.limit.value: ("exclusion", "eligibility", "application", "claim"),
    Intent.claim.value: ("documents", "exclusion", "coverage", "contact"),
    Intent.application.value: ("offer", "coverage", "exclusion", "eligibility"),
    Intent.price.value: ("offer", "application", "coverage", "limit"),
    Intent.offer.value: ("application", "coverage", "exclusion", "claim"),
    Intent.eligibility.value: ("application", "coverage", "offer", "claim"),
    Intent.renewal.value: ("claim", "coverage", "contact", "exclusion"),
    Intent.definition.value: ("coverage", "exclusion", "claim", "application"),
    Intent.document.value: ("coverage", "exclusion", "claim", "contact"),
    Intent.entity.value: ("coverage", "claim", "application", "offer"),
    Intent.unknown.value: ("coverage", "exclusion", "claim", "application"),
}

#: The question each topic becomes, in the product's own name.
QUESTIONS: dict[str, str] = {
    "coverage": "What does {product} cover?",
    "exclusion": "What does {product} not cover?",
    "claim": "How do I make a claim on {product}?",
    "documents": "What documents do I need to claim on {product}?",
    "limit": "What are the cover limits for {product}?",
    "offer": "Is there a promotion for {product}?",
    "application": "How do I buy {product}?",
    "eligibility": "Who can buy {product}?",
    "contact": "How do I contact you about {product}?",
}


def servable(page: Page | None, today: dt.date) -> bool:
    return bool(
        page is not None
        and page.frontmatter.status is Status.approved
        and page.frontmatter.is_effective_on(today)
        and not page.frontmatter.is_review_overdue(today)
    )


def _available(bundle: Bundle, product: Page, *, today: dt.date | None = None) -> set[str]:
    """Only topics backed by approved, current pages; promotions use registry links."""
    today = today or dt.date.today()
    if not servable(product, today):
        return set()
    pid, key = product.id, bundle.product_key(product)

    def has(page_id: str) -> bool:
        return servable(bundle.get(page_id), today)

    topics = {"coverage", "contact"}
    if has(f"{pid}/exclusions"):
        topics.add("exclusion")
    if has(f"{pid}/claims") or has(f"journey/claim/{key}"):
        topics |= {"claim", "documents"}
    if has(f"{pid}/benefits") or has(f"{pid}/cover"):
        topics.add("limit")
    if product.frontmatter.channels:
        topics.add("application")
    if has(f"{pid}/eligibility") or has(f"{pid}/faq"):
        topics.add("eligibility")
    return topics


def product_label(product: Page) -> str:
    return product.frontmatter.title.split(" — ")[0]


def suggest_next(
    bundle: Bundle,
    ask: Ask | None,
    product: Page | None,
    *,
    clarifying: bool = False,
    today: dt.date | None = None,
    answered: frozenset[str] = frozenset(),
) -> list[str]:
    """Up to four questions the customer could ask next."""
    if clarifying:
        # The chips on a clarifying answer are the options themselves.
        return []
    if product is None or ask is None:
        from api.navigation import starter_questions

        return starter_questions(bundle, today or dt.date.today(), MAX_SUGGESTIONS)
    available = _available(bundle, product, today=today)
    name = product_label(product)
    key = "overview" if ask.scope == "overview" else ask.intent.value
    out: list[str] = []
    for topic in NEXT_TOPICS.get(key, NEXT_TOPICS[Intent.unknown.value]):
        if topic in available and topic != ask.intent.value and topic not in answered:
            out.append(QUESTIONS[topic].format(product=name))
        if len(out) == MAX_SUGGESTIONS:
            break
    return out


def closing_question(suggestions: list[str]) -> str:
    """The proactive line an introduction ends on, built from the chips so
    the words on screen and the taps offered agree."""
    topics: list[str] = []
    for s in suggestions[:3]:
        lowered = s.lower()
        if "not cover" in lowered:
            topics.append("what's not covered")
        elif "claim" in lowered:
            topics.append("how to make a claim")
        elif "promotion" in lowered:
            topics.append("current promotions")
        elif "buy" in lowered:
            topics.append("how to buy")
        elif "limits" in lowered:
            topics.append("the cover limits")
        elif "cover" in lowered:
            topics.append("what it covers")
    if not topics:
        return "What would you like to know more about?"
    if len(topics) == 1:
        return f"Would you like to know about {topics[0]}?"
    return f"What would you like to know more about — {', '.join(topics[:-1])}, or {topics[-1]}?"
