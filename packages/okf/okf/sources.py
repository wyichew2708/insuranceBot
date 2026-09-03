"""Which raw sources may support an answer.

The corpus holds two kinds of material. The product page and the documents —
policy wordings, product summaries, brochures, benefit tables, the published
FAQ, the claims and servicing pages — say what a product is and does, and a
customer relying on them is relying on the insurer's own statement of the
cover. Everything else the crawl brought back is marketing: 586 blog posts,
press releases, awards, "about us", tag and category indexes. A blog post
about choosing travel insurance is not a statement of what the policy covers,
and a sentence from one must never be the thing a customer's answer rests on.

This module is the one place that distinction is drawn. The raw-corpus
search reads it, the compiler reads it when it chooses sentences for concept
and channel pages, and a gate reads it after the answer is composed — so a
marketing sentence that somehow reached a page still cannot reach a customer.

A promotion is marketing too, with one exception: a customer who asks whether
there is an offer is asking about the promotion, and the promotion page is
the insurer's own statement of it.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

#: Document tiers under `raw/`. Every file here is the insurer's own
#: statement of a product.
DOCUMENT_TIERS = (
    "raw/wordings/",
    "raw/product-summaries/",
    "raw/brochures/",
    "raw/faq/",
    "raw/benefit-tables/",
)
#: Crawled page types that are the product's own pages.
PRODUCT_PAGE_TYPES = frozenset({"product", "claims", "faq", "servicing"})
#: Crawled page types that are offers.
OFFER_PAGE_TYPES = frozenset({"promo", "promotion"})

#: The classes a source can be. Only the first three may support a claim,
#: and `offer` only when the question is about an offer.
DOCUMENT = "document"
PRODUCT_PAGE = "product_page"
OFFER = "offer"
MARKETING = "marketing"
UNKNOWN = "unknown"

_MARKETING_NAME_RE = re.compile(r"^(?:blog|press[-_]release|news|article|stories|tags?|categor)", re.I)
_PAGE_TYPE_RE = re.compile(r'^page_type:\s*"?([a-z_]+)"?\s*$', re.M)
#: A customer asking about the offer itself.
OFFER_QUESTION_RE = re.compile(
    r"\b(promo|promotion|discount|offer|deal|voucher|cashback|rebate|sale)\b", re.I
)


def page_type_of_text(text: str) -> str:
    """The `page_type` a crawled page's frontmatter declares, or ""."""
    head = text[:2000]
    match = _PAGE_TYPE_RE.search(head)
    return match.group(1) if match else ""


@lru_cache(maxsize=4096)
def _page_type_of_file(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="ignore") as handle:
            return page_type_of_text(handle.read(2000))
    except OSError:
        return ""


def source_class(ref: str, bundle_root: Path | None = None, page_type: str | None = None) -> str:
    """Classify a bundle-relative source ref (`raw/...`, an anchor allowed).

    A web page is classified by its `page_type`, read from the file when the
    caller does not already have it. A ref that is not under `raw/` — a wiki
    page id — is `unknown`, and callers treat that as "not a raw source"
    rather than as marketing.
    """
    base = ref.split("#", 1)[0]
    if not base.startswith("raw/"):
        return UNKNOWN
    if base.startswith(DOCUMENT_TIERS):
        return DOCUMENT
    if base.startswith("raw/web/"):
        kind = page_type
        if kind is None:
            kind = _page_type_of_file(str(bundle_root / base)) if bundle_root is not None else ""
        if kind in PRODUCT_PAGE_TYPES:
            return PRODUCT_PAGE
        if kind in OFFER_PAGE_TYPES:
            return OFFER
        if not kind:
            # An older crawl with no `page_type` — the seed bundle's. The
            # filename still says what a blog or a press release is; anything
            # else is unclassified rather than condemned.
            name = base.rsplit("/", 1)[-1]
            return MARKETING if _MARKETING_NAME_RE.match(name) else UNKNOWN
        return MARKETING
    return UNKNOWN


def may_support(
    ref: str, question: str, bundle_root: Path | None = None, page_type: str | None = None
) -> bool:
    """May this source stand behind a claim in an answer to this question?"""
    kind = source_class(ref, bundle_root, page_type)
    if kind in (DOCUMENT, PRODUCT_PAGE, UNKNOWN):
        # Unknown is "nothing is known against it", not "marketing": a wiki
        # page id, or a crawl too old to carry a page type.
        return True
    if kind == OFFER:
        return bool(OFFER_QUESTION_RE.search(question or ""))
    return False
