"""Who underwrites this — answered from the entity page, never composed.

"Who is the insurer behind Etiqa Autolab Package Wic?" was answered with a
fire-peril clause: "If the property hereby insured shall at the breaking out
of any fire…". Every gate passed. The question classified as `unknown`, which
the answerability gate treats as unconstrained, and retrieval ranked the
product's own conditions page above the one page in the bundle that actually
says who the insurer is. Fourteen of seventy-nine unsafe cases on the real
corpus were this shape.

The bundle has exactly one entity page and every one of its 670 product pages
names the same underwriter. That is not a retrieval problem to solve better;
it is a fact to state. So this answers the way a greeting does — deterministic,
one claim, bound to the entity page — and the composer never sees the turn.
"""

from __future__ import annotations

from harness import Claim, GroundedAnswer

from okf import Bundle, Page, PageType, Status


def _entity_page(bundle: Bundle) -> Page | None:
    for page in bundle.pages.values():
        if page.frontmatter.type == PageType.entity and page.frontmatter.status == Status.approved:
            return page
    return None


def answer(bundle: Bundle) -> GroundedAnswer | None:
    """The underwriter, or None if this bundle does not declare one.

    None rather than a guess: a bundle with no approved entity page, or with
    products under more than one underwriter, is one this cannot speak for,
    and the turn falls through to the ordinary path where the answerability
    gate will demand the entity page and refuse without it.
    """
    page = _entity_page(bundle)
    if page is None:
        return None
    underwriters = {
        p.frontmatter.underwriter
        for p in bundle.pages.values()
        if p.frontmatter.type == PageType.product and p.frontmatter.underwriter
    }
    if len(underwriters) > 1:
        return None
    name = page.frontmatter.title
    uen = page.frontmatter.uen
    text = f"Every policy we sell is underwritten by {name}"
    text += f" (UEN {uen})." if uen else "."
    text += " That is the company you hold the contract with, and the one that pays a claim."
    return GroundedAnswer(
        answer=text,
        claims=[Claim(text=name, source_id=page.id, locator=page.id)],
        confidence=1.0,
    )
