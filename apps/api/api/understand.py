"""Which product the customer is asking about (DESIGN-answering.md §4.1).

Lexical ranking decides this today, and it decides it by counting words
weighted by how rare they are in the corpus. That is the wrong signal and no
coefficient fixes it: measured on the real bundle, `want` scores 0.791 and
`cancer` scores 0.408, because a conversational verb is vanishingly rare in a
corpus of contracts. "Want to buy cancer insurance" was ranked onto the
home-insurance FAQ, whose headings are full of "I want to buy".

A model reads that question correctly without trying. So it is asked — under
three constraints that keep the safety properties the rest of the system
depends on.

**It selects; it never asserts.** The model is handed a shortlist of product
ids that exist in this bundle and returns ids from it. An id that does not
resolve is discarded. This is the same line the system draws at generation
time, where the model phrases facts and never establishes one — selection
from a closed set is checkable by existence, and a fact is not.

**It can only improve selection.** No model, a timeout, malformed output, an
id that does not resolve: every one of those falls through to the lexical
path that runs today. There is no configuration in which this makes retrieval
worse than it already is.

**The deterministic path stays complete.** `DeterministicProvider.classify`
returns None, so CI and the eval suites keep running offline and free, and the
suite still measures the retrieval everyone else gets.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from okf import Bundle, Page, PageType, Status

#: How many products to offer the model. Enough that the right one is almost
#: always present, small enough that the prompt stays cheap and the model is
#: choosing rather than searching.
SHORTLIST = 25

SYSTEM_PROMPT = """\
You match an insurance customer's question to the products it is about.

You are given a numbered list of products that exist. Reply with the ids of \
the ones the question is about — normally exactly one.

Rules:
1. Only ids from the list. Never invent one, never adapt one.
2. If the question names a product plainly, return that product and nothing \
else, however the customer phrased the request around it.
3. If two or more products are genuinely plausible and the question does not \
separate them, return all of them and set `ambiguous` true. A customer asked \
which one they meant is better served than one given a guess.
4. If the question is about no product on the list — a general question, a \
greeting, something off-topic — return an empty list.

You are identifying a subject, not answering. Say nothing about cover, \
limits, exclusions or price.
"""

SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["product_ids", "ambiguous"],
    "properties": {
        "product_ids": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": 4,
            "description": "Ids copied verbatim from the list. Empty if none apply.",
        },
        "ambiguous": {
            "type": "boolean",
            "description": "True when the question does not separate the ids returned.",
        },
    },
}


@dataclass(frozen=True)
class Understanding:
    """What the model made of the question, after verification."""

    product_ids: list[str] = field(default_factory=list)
    ambiguous: bool = False
    #: Why this is empty, when it is. Recorded on the trace so a turn that fell
    #: back to lexical says which of the ways it did so.
    degraded: str = ""

    @property
    def resolved(self) -> bool:
        return bool(self.product_ids)


def shortlist(bundle: Bundle, question: str, limit: int = SHORTLIST) -> list[Page]:
    """Products worth offering the model, best lexical guesses first.

    The lexical layer is poor at *picking* and perfectly adequate at
    *narrowing*: the right product was in the top 25 in every failure this
    module exists to fix — it was ranked second, or sixth, not absent.
    """
    from api.retrieval import score_page, subject_terms
    from okf import term_idf

    terms = subject_terms(question)
    products = [
        page
        for page in bundle.pages.values()
        if page.frontmatter.type == PageType.product
        and page.frontmatter.status == Status.approved
        and page.id.count("/") == 2
    ]
    if not terms:
        return sorted(products, key=lambda p: p.id)[:limit]
    idf = term_idf(bundle)
    ranked = sorted(products, key=lambda p: (-score_page(p, terms, idf=idf), p.id))
    return ranked[:limit]


def _catalogue(pages: list[Page]) -> str:
    lines = []
    for page in pages:
        aliases = ", ".join(page.frontmatter.aliases[:4])
        suffix = f"  (also called: {aliases})" if aliases else ""
        lines.append(f"- {page.id} — {page.frontmatter.title}{suffix}")
    return "\n".join(lines)


def understand(bundle: Bundle, question: str, provider: Any) -> Understanding:
    """Resolve the question to product ids, or explain why it could not.

    Never raises. Every failure path returns an empty `Understanding` with
    `degraded` set, and the caller carries on with lexical retrieval.
    """
    classify = getattr(provider, "classify", None)
    if classify is None or getattr(provider, "name", "") == "deterministic":
        return Understanding(degraded="no model")

    candidates = shortlist(bundle, question)
    if not candidates:
        return Understanding(degraded="no products to choose from")

    try:
        payload = classify(
            SYSTEM_PROMPT,
            f"PRODUCTS:\n{_catalogue(candidates)}\n\nQUESTION: {question}",
            SCHEMA,
            max_tokens=256,
        )
    except Exception as exc:  # a provider fault is a degraded turn, never a failed one
        return Understanding(degraded=f"{type(exc).__name__}")
    if not isinstance(payload, dict):
        return Understanding(degraded="no verdict")

    offered = {page.id for page in candidates}
    # Verified against the shortlist, not merely against the bundle: a model
    # that answers with a product it was not offered has not selected, it has
    # recalled — and recall is the failure mode this design exists to exclude.
    ids = [
        value
        for value in payload.get("product_ids", [])
        if isinstance(value, str) and value.strip() in offered
    ]
    if not ids:
        return Understanding(degraded="no id resolved")
    return Understanding(product_ids=ids, ambiguous=bool(payload.get("ambiguous")))


#: A question that names no product at all — a greeting, an off-topic aside —
#: should not be handed a product just because one ranked highest.
_TRIVIAL = re.compile(r"^[\W\d]*$")


def worth_resolving(question: str) -> bool:
    return not _TRIVIAL.match(question or "")
