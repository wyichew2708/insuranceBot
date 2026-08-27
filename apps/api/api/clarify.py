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

from okf import Bundle, Page, PageType

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


#: Beyond this many near-ties, listing them is a menu rather than a question.
#: "How do I make a claim" ties 87 products on the real bundle — every one of
#: them answers it, which is precisely why none of them is the answer.
LISTABLE = 6

OPEN_QUESTION = (
    "Which policy is this about? I cover a fair range — travel, home, car, maid, "
    "life, health and business insurance among them — and the answer is different "
    "for each, so tell me which one and I'll give you the detail."
)


def open_clarification() -> GroundedAnswer:
    """Ask which product, without naming any.

    A question that ties dozens of products has not named a subject, and the
    system's own machinery says so — the lexical scorer puts 87 products on an
    identical score for "how do i make a claim". Choosing one of them was the
    old behaviour and it is how a customer asking a plain question was answered
    about Plate Glass. There is nothing to cite, because nothing is claimed.
    """
    return GroundedAnswer(answer=OPEN_QUESTION, claims=[], clarifying=True, confidence=1.0)


def _root_page(bundle: Bundle, product_key: str) -> Page | None:
    """The `product/<line>/<slug>` page for a product key."""
    for page in bundle.pages.values():
        if (
            page.id.count("/") == 2
            and page.frontmatter.type == PageType.product
            and bundle.product_key(page) == product_key
        ):
            return page
    return None


def lexical_clarification(bundle: Bundle, product_keys: list[str]) -> GroundedAnswer | None:
    """Ask about a tie the lexical layer could not break.

    A short tie is a real choice and gets named options. A long one is not:
    it means the question named no product, so naming three of the eighty-seven
    that tied would be arbitrary in exactly the way this is meant to stop.
    """
    if len(product_keys) < 2:
        return None
    if len(product_keys) > LISTABLE:
        return open_clarification()
    pages = [page for page in (_root_page(bundle, key) for key in product_keys) if page is not None]
    return clarification(bundle, [page.id for page in pages])


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
