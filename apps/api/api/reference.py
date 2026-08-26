"""Resolving what "it" refers to.

A customer types "term life", reads the answer, and then asks "what's the
coverages". The second turn is about term life and says so nowhere. Until this
existed the session carried channel, auth level and policy but not *topic*, so
that turn retrieved on four words that name no product and was refused.

Measured across three bundles, this is the largest gap in the conversation
suite: context-dependent turns pass 96.8% on the seed bundle and 48.4% on the
real one. The seed's score was never reference resolution working — it is a
corpus of three products, where failing to resolve a reference still lands on
the right page.

Two decisions shape this module.

**The history comes from the client.** The service stays stateless: no session
store, no per-session memory to lose on restart or to leak between users. The
chat surface already holds the transcript, so it sends it, and a turn is
reproducible from its own request.

**Context is used only when the turn cannot stand alone.** Carrying the last
topic into every turn is how "what about car insurance?" gets answered about
term life. A turn that names its own subject is left exactly as the customer
typed it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from okf import Bundle, PageType, Status

#: How far back to look. Two turns covers "term life" → "what's the coverages"
#: → "and the exclusions?", which is the shape these conversations take. More
#: history is more chances to carry a stale topic into a new one.
LOOKBACK = 3

#: Words that carry no subject: interrogatives, articles, and the vocabulary of
#: asking itself. A turn made only of these is asking about something it does
#: not name.
_EMPTY = frozenset(
    [
        "what",
        "whats",
        "what's",
        "which",
        "who",
        "when",
        "where",
        "why",
        "how",
        "is",
        "are",
        "was",
        "were",
        "do",
        "does",
        "did",
        "can",
        "could",
        "would",
        "should",
        "will",
        "shall",
        "may",
        "might",
        "must",
        "have",
        "has",
        "had",
        "the",
        "a",
        "an",
        "and",
        "or",
        "but",
        "of",
        "for",
        "to",
        "in",
        "on",
        "at",
        "by",
        "with",
        "from",
        "about",
        "that",
        "this",
        "these",
        "those",
        "it",
        "its",
        "i",
        "me",
        "my",
        "you",
        "your",
        "we",
        "us",
        "our",
        "they",
        "them",
        "their",
        "there",
        "here",
        "tell",
        "show",
        "give",
        "explain",
        "say",
        "list",
        "me",
        "more",
        "other",
        "another",
        "also",
        "please",
        "thanks",
        "ok",
        "okay",
        "yes",
        "no",
        "not",
        "sure",
        "any",
        "some",
        "all",
        "much",
        "many",
        "long",
        "far",
        "cost",
        "costs",
        "price",
        "coverage",
        "coverages",
        "cover",
        "covers",
        "covered",
        "covering",
        "exclusion",
        "exclusions",
        "excluded",
        "claim",
        "claims",
        "claiming",
        "benefit",
        "benefits",
        "limit",
        "limits",
        "premium",
        "premiums",
        "renew",
        "renewal",
        "cancel",
        "eligibility",
        "eligible",
        "apply",
        "application",
        "buy",
    ]
)


@dataclass(frozen=True)
class Resolution:
    """A question, and what it had to borrow to make sense."""

    question: str
    carried_from: str | None = None

    @property
    def resolved(self) -> bool:
        return self.carried_from is not None


def _subject_terms(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", (text or "").lower())
    return {w for w in words if w not in _EMPTY and len(w) > 2}


def names_a_subject(question: str, bundle: Bundle) -> bool:
    """Does this turn name something the corpus knows about?

    Checked against the corpus rather than against a word list, because the
    question is not "is this a noun" but "can retrieval do anything with it".
    "what's the coverages" has a subject word by any grammatical test —
    `coverages` — and names no product at all.
    """
    terms = _subject_terms(question)
    if not terms:
        return False
    for page in bundle.pages.values():
        fm = page.frontmatter
        if fm.type != PageType.product or fm.status != Status.approved:
            continue
        surface = f"{fm.title} {' '.join(fm.aliases)} {page.id.replace('/', ' ').replace('-', ' ')}".lower()
        if any(re.search(rf"(?<![a-z0-9]){re.escape(t)}(?![a-z0-9])", surface) for t in terms):
            return True
    return False


def resolve(question: str, history: list[str], bundle: Bundle) -> Resolution:
    """The question with its subject restored, where it had none.

    The borrowed text is *prepended*, not substituted: "term life" + "what's
    the coverages" retrieves on both, so the topic selects the product and the
    customer's own words still select the section. Substituting the topic
    would answer a question they did not ask.
    """
    if not history or names_a_subject(question, bundle):
        return Resolution(question=question)

    # Most recent first: the nearest turn that named something is the topic.
    for earlier in reversed(history[-LOOKBACK:]):
        if names_a_subject(earlier, bundle):
            subject = " ".join(w for w in earlier.split() if w.lower() not in _EMPTY)
            if subject:
                return Resolution(question=f"{subject} {question}", carried_from=earlier)
    return Resolution(question=question)
