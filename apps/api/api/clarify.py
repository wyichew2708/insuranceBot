"""Asking which product was meant, instead of picking one (§4.5).

`focus_product` picks the highest-scoring product and then **excludes every
other one from retrieval**. That is how "cancer insurance" was answered from
the pet-insurance FAQ: the two tied, an alphabetical tiebreak went to pet, and
the cancer page was then filtered out as "a different product". One coin toss,
and the right evidence was unreachable for the rest of the turn.

The tiebreak is fixed. The shape of the mistake is not: a system that must
always choose will sometimes choose wrong and say so with complete confidence.
For an assistant answering questions about somebody's insurance, that is the
failure that matters — and it has a cheap remedy that this system could not
perform at all until now.

A clarifying question is never wrong. It costs the customer one turn and it
cannot mislead them. So where the question genuinely does not separate two
products, the honest answer is to ask.

It is a real answer, not a special case: it names products, each one a claim
bound to the page it came from, and it goes through the same eight gates as
anything else. What it does not do is assert anything about cover — so the
coverage gates find nothing to check, which is correct, because nothing has
been claimed about cover yet.
"""

from __future__ import annotations

from harness import Claim, GroundedAnswer

from okf import Bundle, Page

#: More than this and it is not a question, it is a menu. The model is asked
#: for at most four; beyond three the honest move is to list rather than ask.
MAX_OPTIONS = 3


def _name(page: Page) -> str:
    """The product's own name, never a child page's heading."""
    return page.frontmatter.title.split(" — ")[0]


def question_for(products: list[Page]) -> str:
    names = [_name(p) for p in products]
    if len(names) == 2:
        choices = f"{names[0]} or {names[1]}"
    else:
        choices = ", ".join(names[:-1]) + f", or {names[-1]}"
    return (
        f"I want to be sure I answer about the right one — did you mean {choices}? "
        "Tell me which and I'll give you its cover, exclusions or claim steps."
    )


def clarification(bundle: Bundle, product_ids: list[str]) -> GroundedAnswer | None:
    """Ask which of these was meant, or None if there is nothing to ask about.

    None where fewer than two products resolve: one product is not a choice,
    and none is not a question. The caller then carries on as it would have.
    """
    products = [page for page in (bundle.get(pid) for pid in product_ids) if page is not None]
    if len(products) < 2:
        return None
    shown = products[:MAX_OPTIONS]
    return GroundedAnswer(
        # One claim per option, so every name in the question resolves to the
        # page it was read from and reference-integrity has something to check.
        answer=question_for(shown),
        claims=[Claim(text=_name(p), source_id=p.id, locator=p.id) for p in shown],
        clarifying=True,
        confidence=1.0,
    )
