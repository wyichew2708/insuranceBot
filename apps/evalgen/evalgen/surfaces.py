"""Question surfaces — one fact, asked the way different customers ask it.

A benefit-table row supports one *fact* and many *questions*. Asking each fact
once measures whether the corpus is reachable at all; asking it a dozen ways
measures whether it is reachable by a customer who does not already speak the
product's vocabulary — which is the only kind of customer there is.

Every surface of a fact carries the *same* expectation, so a family is a
controlled experiment: the figure that comes back and the evidence it rests on
must not move when the wording does. When they do move, the report names the
phrasing that broke it rather than a bare accuracy drop.

Surfaces are `strict` or not, and the distinction is about fairness rather than
difficulty. A strict surface names the product and the benefit, so demanding an
exact citation is reasonable. A loose one ("baggage limit?", "the airline lost
my suitcase") deliberately withholds that vocabulary; there the figure must
still be exactly right, but *which* page the composer cites is scored through
the retrieval metrics instead of asserted — the same treatment the alias
families already get, and for the same reason: several legitimate pages carry
the answer, so pinning one of them would measure the fixture, not the bot.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from evalgen.schema import Category

#: Phrases that would mean the assistant made the customer pick a brand.
#: `www.etiqa.com.sg` and `www.tiq.com.sg` are two front doors of one direct
#: channel selling one set of products, so a customer starts from the product
#: and never has to answer "which of the two?". Any of these in an answer is a
#: regression however helpfully the surrounding sentence is worded.
BRAND_CONFUSION: tuple[str, ...] = (
    "etiqa or tiq",
    "tiq or etiqa",
    "which brand",
    "etiqa product or a tiq",
    "tiq product or an etiqa",
    "are you asking about etiqa",
    "are you asking about tiq",
)


@dataclass(frozen=True)
class Surface:
    """One phrasing of a fact."""

    kind: str
    question: str
    category: Category
    strict: bool = True
    #: Extra `must_contain` strings that only this phrasing earns. Asking who
    #: to call is entitled to a hotline in the answer; asking whether a route
    #: exists at all is not, and asserting one on both would fail the bot for
    #: answering the question that was asked.
    asserts: tuple[str, ...] = ()


def short(title: str) -> str:
    """``'Travel Insurance'`` -> ``'travel'``. Customers rarely type the whole
    product name, and the short form is what shows up in search-style queries."""
    return re.sub(r"\s*insurance\s*$", "", title, flags=re.I).strip().lower()


def _rotate(items: Sequence[str], seed: int) -> str | None:
    """Pick from a list by position so successive facts exercise different
    vocabulary instead of hammering the first alias every time."""
    return items[seed % len(items)] if items else None


# Attributes whose value is a ceiling, so "what is the most it pays" is a fair
# rephrasing. `excess` and `threshold_hours` are floors and are excluded.
CEILING_ATTRIBUTES = frozenset({"limit", "cap", "per_item_limit", "max_percentage"})

#: A situation instead of a term. This is the phrasing that separates a system
#: that matches vocabulary from one that resolves intent — the customer names
#: what happened to them and never names the benefit.
SCENARIO: dict[str, str] = {
    "travel_delay": "My flight home was delayed overnight. What does {short} cover me for?",
    "baggage_loss": "The airline lost my suitcase. How much can I claim under {short}?",
    "medical_expenses": "I ended up in hospital while I was abroad. What does {short} pay?",
    "trip_cancellation": "I had to call off my trip at the last minute. What do I get back on {short}?",
    "contents": "My place was broken into and things were taken. What does {short} pay out?",
    "alternative_accommodation": (
        "There was a fire and I cannot live in my flat. Does {short} pay for somewhere to stay?"
    ),
    "own_damage": "I reversed into a pillar. What do I have to pay myself before {short} covers it?",
    "ncd": "I have not claimed in years. How far can my discount go on {short}?",
}


@dataclass(frozen=True)
class FigureFact:
    """Everything needed to phrase one benefit-table row many ways."""

    title: str
    benefit_code: str
    benefit: str
    attribute: str
    attribute_text: str
    value: str
    tier: str
    canonical: str
    aliases: tuple[str, ...] = ()
    brands: tuple[str, ...] = ()
    seed: int = 0


def figure_surfaces(fact: FigureFact) -> list[Surface]:
    """The phrasing family for one benefit-table row.

    Ordered canonical-first so the family's id is stable when a later surface
    stops applying — a row that loses its scenario template keeps every other
    question's id, and the report can still diff against the previous run.
    """
    name = fact.title
    stub = short(name)
    out: list[Surface] = [Surface("canonical", fact.canonical, Category.figure)]

    out.append(
        Surface(
            "plain",
            f"How much does {name} pay out for {fact.benefit}?",
            Category.figure,
        )
    )
    out.append(
        Surface(
            "polar",
            f"Is there a {fact.attribute_text} on {fact.benefit} under {name}?",
            Category.figure,
        )
    )
    # Keyword-shaped, the way a search box gets used. No grammar, no product
    # suffix, no article — the hardest input for a retriever that leans on
    # sentence structure.
    out.append(
        Surface(
            "elliptical",
            f"{stub} {fact.benefit} {fact.attribute_text}",
            Category.figure,
            strict=False,
        )
    )
    # The customer states the figure back. Confirming a correct number is a
    # different failure mode from rejecting a wrong one (which the conflict
    # family covers): the temptation here is to agree without re-fetching.
    out.append(
        Surface(
            "verification",
            f"I was told the {fact.benefit} {fact.attribute_text} on {name} is {fact.value}. Is that right?",
            Category.figure,
        )
    )

    if fact.attribute in CEILING_ATTRIBUTES:
        out.append(
            Surface(
                "superlative",
                f"What is the most {name} will pay for {fact.benefit}?",
                Category.figure,
            )
        )

    if fact.tier not in {"", "ALL"}:
        out.append(
            Surface(
                "tiered",
                f"On {fact.tier} of {name}, what is the {fact.benefit} {fact.attribute_text}?",
                Category.figure,
            )
        )

    scenario = SCENARIO.get(fact.benefit_code)
    if scenario:
        out.append(Surface("scenario", scenario.format(short=stub), Category.figure, strict=False))

    alias = _rotate(fact.aliases, fact.seed)
    if alias:
        out.append(
            Surface(
                "alias",
                f"For my {alias}, how much is the {fact.benefit} {fact.attribute_text}?",
                Category.alias,
            )
        )

    brand = _rotate(fact.brands, fact.seed)
    if brand:
        # The question the merge has to survive. It names one front door; the
        # answer must simply answer, never ask which of the two the customer
        # means.
        out.append(
            Surface(
                "brand",
                f"What is the {fact.benefit} {fact.attribute_text} on {brand} {stub} insurance?",
                Category.brand,
                strict=False,
            )
        )
    return out


def exclusion_surfaces(title: str, subject: str, seed: int, brands: Sequence[str] = ()) -> list[Surface]:
    """Phrasings for one excluded thing.

    An exclusion is asked far more often in the negative and in the past tense
    than as a coverage lookup — the customer has already had the loss.
    """
    stub = short(title)
    out = [
        Surface("canonical", f"Are {subject} covered under {title}?", Category.exclusion),
        Surface("negative", f"Is {subject} excluded from {title}?", Category.exclusion),
        Surface("claim", f"I am claiming for {subject}. Will {title} pay?", Category.exclusion, strict=False),
        Surface("elliptical", f"{stub} {subject} excluded?", Category.exclusion, strict=False),
        Surface("scope", f"Does {title} pay out for {subject}, or is that on me?", Category.exclusion),
    ]
    brand = _rotate(list(brands), seed)
    if brand:
        out.append(
            Surface(
                "brand",
                f"Does {brand} {stub} insurance exclude {subject}?",
                Category.brand,
                strict=False,
            )
        )
    return out


def section_surfaces(title: str, heading: str) -> list[Surface]:
    """Phrasings for a section the product page publishes under its own
    heading. The corpus wrote the heading; these ask for it back."""
    stub = short(title)
    low = heading.lower()
    return [
        Surface("canonical", f"What does {title} say about {low}?", Category.coverage),
        Surface("direct", f"{title} — {low}?", Category.coverage),
        Surface("elliptical", f"{stub} {low}", Category.coverage, strict=False),
        Surface(
            "explain", f"Can you walk me through {low} for {stub} insurance?", Category.coverage, strict=False
        ),
    ]


def alias_surfaces(alias: str) -> list[Surface]:
    """An alias is only useful if it resolves from more than one sentence
    shape, so each is asked as a request, a question and a bare term."""
    return [
        Surface("canonical", f"Tell me about {alias}.", Category.alias),
        Surface("question", f"What is {alias} and what does it give me?", Category.alias),
        Surface("elliptical", alias, Category.alias, strict=False),
    ]


def concept_surfaces(title: str, concept_title: str) -> list[Surface]:
    """A concept asked *through* a product. The concept page holds the
    definition and the product page holds the number, so these are the cases
    where the answer has to come from two pages at once."""
    stub = short(title)
    low = concept_title.lower()
    return [
        Surface("applies", f"How does {low} work on {title}?", Category.concept),
        Surface("polar", f"Does {title} have {low}?", Category.concept),
        Surface(
            "amount", f"What {low} applies if I claim on {stub} insurance?", Category.concept, strict=False
        ),
        Surface(
            "define", f"Explain {low} in the context of {stub} insurance.", Category.concept, strict=False
        ),
    ]


def channel_surfaces(title: str, route: str, hotline: str | None = None) -> list[Surface]:
    """Buying one product down one route.

    Contact details are a verbatim-only zone, so the hotline is asserted on the
    phrasing that actually asks for it and nowhere else.
    """
    stub = short(title)
    return [
        Surface("buy", f"How do I buy {title} through the {route} channel?", Category.channel),
        Surface(
            "contact",
            f"Who do I call about {stub} insurance bought via {route}?",
            Category.channel,
            asserts=(hotline,) if hotline else (),
        ),
        Surface("route", f"Can I get {title} from a {route.lower()}?", Category.channel, strict=False),
        Surface(
            "where",
            f"Where do I go to arrange {stub} cover on the {route} route?",
            Category.channel,
            strict=False,
        ),
    ]


def entity_surfaces(title: str) -> list[Surface]:
    """Who carries the risk. One underwriter sits behind every route, and if
    the assistant cannot say so the merge story is decorative."""
    stub = short(title)
    return [
        Surface("insurer", f"Who is the insurer behind {title}?", Category.entity),
        Surface("underwriter", f"Which company underwrites {stub} insurance?", Category.entity),
        Surface(
            "legal", f"If I claim on {title}, which legal entity am I claiming against?", Category.entity
        ),
    ]


def coverage_surfaces(title: str, brands: Sequence[str] = ()) -> list[Surface]:
    """The opening question of most real conversations."""
    stub = short(title)
    out = [
        Surface("canonical", f"What does {title} cover?", Category.coverage),
        Surface("scope", f"What is included in {stub} insurance?", Category.coverage),
        Surface("negative", f"What is not covered by {title}?", Category.coverage),
        Surface("elliptical", f"{stub} insurance coverage", Category.coverage, strict=False),
        Surface(
            "summary",
            f"Give me the short version of what {stub} cover does.",
            Category.coverage,
            strict=False,
        ),
    ]
    for brand in brands:
        # The exact shape the brand merge was built for: a customer naming one
        # front door and expecting an answer, not a disambiguation prompt.
        out.append(
            Surface(
                "brand",
                f"Does {brand} sell {stub} insurance, and what does it cover?",
                Category.brand,
                strict=False,
            )
        )
    return out


def advice_surfaces(title: str) -> list[Surface]:
    """Requests for a recommendation. Every one of these must trip the advice
    boundary; none of them may be answered with a product choice."""
    stub = short(title)
    return [
        Surface("which", f"Which {title} plan should I buy for my family?", Category.advice),
        Surface("enough", f"Is {stub} insurance enough cover for someone like me?", Category.advice),
        Surface("recommend", f"What {stub} cover do you recommend I take?", Category.advice),
        Surface("worth", f"Would you say {title} is worth it for me?", Category.advice),
    ]


@dataclass(frozen=True)
class GapProbe:
    """A question every customer asks, whether or not the corpus answers it.

    Where the bundle carries the page, the answer must cite it. Where it does
    not, the only correct answer is a handoff — and the failure these catch is
    the confident one: a renewal date, a premium, a claims phone number that
    reads perfectly and came from nowhere. `needs` names the structure that
    would make the question answerable, so which side of the line a probe falls
    on is decided by the bundle rather than written down here.
    """

    key: str
    #: Structure the product page must carry for this to be answerable, or
    #: None for intents this corpus models for no product at all.
    needs: str | None
    templates: tuple[str, ...]


GAP_PROBES: tuple[GapProbe, ...] = (
    GapProbe(
        "claim",
        "claims",
        (
            "How do I make a claim on {title}?",
            "What do I need to submit to claim under {short} insurance?",
        ),
    ),
    GapProbe(
        "buy",
        "buy",
        (
            "What are the steps to take out {title}?",
            "Walk me through buying {short} cover.",
        ),
    ),
    GapProbe(
        "freelook",
        "free-look",
        (
            "Can I change my mind after buying {title}?",
            "Is there a cooling-off period on {short} insurance?",
        ),
    ),
    # The most-asked question about any insurance product, and the one with no
    # answer anywhere in this corpus. A premium invented here would be both the
    # most convincing and the most costly kind of wrong.
    GapProbe(
        "premium",
        None,
        (
            "How much does {title} cost me a year?",
            "What is the premium for {short} insurance?",
        ),
    ),
    GapProbe(
        "renewal",
        None,
        (
            "When does {title} renew, and will it renew by itself?",
            "Do I have to do anything to renew my {short} cover?",
        ),
    ),
    GapProbe(
        "document",
        None,
        (
            "Where do I download the policy document for {title}?",
            "Can you send me the full {short} policy wording?",
        ),
    ),
)


def gap_surfaces(probe: GapProbe, title: str) -> list[Surface]:
    stub = short(title)
    return [
        Surface(
            f"{probe.key}-{n}" if n else probe.key,
            template.format(title=title, short=stub),
            Category.journey if probe.needs else Category.out_of_scope,
            strict=False,
        )
        for n, template in enumerate(probe.templates)
    ]
