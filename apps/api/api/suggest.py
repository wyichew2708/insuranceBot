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

from harness.ask import Ask
from harness.intent import Intent

from okf import Bundle, Page, PageType

#: How many chips to offer.
MAX_SUGGESTIONS = 4

#: Per intent, the topics worth offering next, in order. `overview` is the
#: introduction a bare product name gets.
NEXT_TOPICS: dict[str, tuple[str, ...]] = {
    "overview": ("exclusion", "claim", "offer", "application", "limit"),
    Intent.coverage.value: ("exclusion", "claim", "limit", "offer"),
    Intent.exclusion.value: ("coverage", "claim", "application", "offer"),
    Intent.limit.value: ("exclusion", "claim", "coverage", "application"),
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

#: When no product is in play — a greeting, an off-topic aside, a question
#: about insurance in general.
STARTERS = (
    "What does Tiq Travel Insurance cover?",
    "What does Tiq Home Insurance not cover?",
    "How do I make a claim on Tiq Maid Insurance?",
    "Is there a promotion for Private Car Insurance?",
)


def _available(bundle: Bundle, product: Page) -> set[str]:
    """Topics the corpus can answer for this product."""
    pid = product.id
    key = bundle.product_key(product)
    topics = {"coverage", "contact"}
    if bundle.get(f"{pid}/exclusions") is not None:
        topics.add("exclusion")
    if bundle.get(f"{pid}/claims") is not None or bundle.get(f"journey/claim/{key}") is not None:
        topics |= {"claim", "documents"}
    if bundle.get(f"{pid}/benefits") is not None or bundle.get(f"{pid}/cover") is not None:
        topics.add("limit")
    if product.frontmatter.channels:
        topics.add("application")
    if bundle.get(f"{pid}/eligibility") is not None or bundle.get(f"{pid}/faq") is not None:
        topics.add("eligibility")
    if any(
        p.frontmatter.type is PageType.promotion and bundle.product_key(p) == key
        for p in bundle.pages.values()
    ):
        topics.add("offer")
    return topics


def product_label(product: Page) -> str:
    return product.frontmatter.title.split(" — ")[0]


def suggest_next(
    bundle: Bundle, ask: Ask | None, product: Page | None, *, clarifying: bool = False
) -> list[str]:
    """Up to four questions the customer could ask next."""
    if clarifying:
        # The chips on a clarifying answer are the options themselves.
        return []
    if product is None or ask is None:
        starters = [q for q in STARTERS if bundle_has(bundle, q)]
        return starters[:MAX_SUGGESTIONS]
    available = _available(bundle, product)
    name = product_label(product)
    key = "overview" if ask.scope == "overview" else ask.intent.value
    out: list[str] = []
    for topic in NEXT_TOPICS.get(key, NEXT_TOPICS[Intent.unknown.value]):
        if topic in available and topic != ask.intent.value:
            out.append(QUESTIONS[topic].format(product=name))
        if len(out) == MAX_SUGGESTIONS:
            break
    return out


def bundle_has(bundle: Bundle, question: str) -> bool:
    """A starter is offered only if its product exists in this bundle."""
    lowered = question.lower()
    return any(
        p.frontmatter.type is PageType.product
        and p.id.count("/") == 2
        and product_label(p).lower() in lowered
        for p in bundle.pages.values()
    )


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
