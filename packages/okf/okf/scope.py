"""Which product a piece of content belongs to, and a scope that enforces it.

The compiled wiki knows its products: every page under `product/<line>/<slug>`
belongs to `<slug>`. The raw sources did not. A wording was matched to a
product by filename when the wiki was compiled and the match was forgotten;
at answer time the RAG fallback walked every file under `raw/` and admitted
by the marketing screen and the in-force version alone. A question about one
product could be answered from another's wording, and on the field test it
was — `cited products ['travel'], expected ['travel-insurance']` is the
suite's largest failure class.

`raw_product_index` rebuilds the tag from what the bundle already carries —
the catalogue's `documents` keys and `urls`, the crawl manifest's canonical
URLs, the benefit-table filenames — once per bundle, cached on it. `Scope`
then admits a page or a document only if it belongs to the product the turn
is about, or to no product at all.

A bundle without a catalogue (the seed) tags nothing, and a scope over
nothing tagged admits everything. That is deliberate: a filter that cannot
tell products apart must not silently drop the corpus.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from okf.bundle import Bundle
from okf.page import Page

#: Tag for material that belongs to the insurer rather than to a product:
#: contact pages, the claims-and-services hub, promotions, policy servicing.
SHARED = "shared"
#: Tag for material the index could not place. Excluded once a scope is set,
#: admitted when nothing at all is tagged.
UNKNOWN = "unknown"

#: Crawled paths that are the insurer's, not a product's.
_SHARED_WEB = re.compile(
    r"/(?:contact-us|claims-and-services|promotions?|policy-services|about(?:-us)?|faq|help|"
    r"customer-service|login|privacy|terms)(?:/|$)",
    re.I,
)
_WORD = re.compile(r"[a-z0-9]+")


def _forms(text: str) -> set[str]:
    """Normalised spellings a name may appear under in a filename or URL."""
    words = _WORD.findall(text.lower())
    if not words:
        return set()
    return {"".join(words), "-".join(words)}


def _norm_url(url: str) -> str:
    return re.sub(r"^https?://(?:www\.)?", "", url.strip().lower()).rstrip("/")


def _catalogue(root: Path) -> list[dict[str, Any]]:
    path = root / "catalogue.yaml"
    if not path.is_file():
        return []
    data = yaml.safe_load(path.read_text()) or {}
    return list(data.get("products") or [])


def _manifest(root: Path) -> dict[str, dict[str, Any]]:
    """Crawl manifest rows by the bundle-relative path they were written to."""
    path = root / "raw" / "web" / "crawl-manifest.json"
    if not path.is_file():
        return {}
    data = json.loads(path.read_text())
    out: dict[str, dict[str, Any]] = {}
    for row in data.get("pages") or []:
        rel = str(row.get("path") or "")
        # Manifest paths are written from the repository root; strip the
        # bundle directory so they compare with `raw/...` refs.
        marker = rel.find("raw/")
        if marker >= 0:
            out[rel[marker:]] = row
    return out


def raw_product_index(bundle: Bundle) -> dict[str, str]:
    """`raw/...` ref → product slug, `shared`, or `unknown`. Cached on the bundle."""
    cached: dict[str, str] | None = getattr(bundle, "_raw_products", None)
    if cached is not None:
        return cached
    root = Path(bundle.root)
    products = _catalogue(root)
    keys: dict[str, set[str]] = {}
    urls: dict[str, str] = {}
    for entry in products:
        slug = str(entry.get("slug") or "")
        if not slug:
            continue
        forms = _forms(slug) | _forms(str(entry.get("name") or ""))
        for key in entry.get("documents") or []:
            forms |= _forms(str(key))
        keys[slug] = {f for f in forms if len(f) >= 4}
        for url in entry.get("urls") or []:
            urls[_norm_url(str(url))] = slug
    manifest = _manifest(root)

    index: dict[str, str] = {}
    raw = root / "raw"
    if not raw.is_dir():
        bundle._raw_products = index  # type: ignore[attr-defined]
        return index
    for path in sorted(p for p in raw.rglob("*") if p.is_file() and p.suffix in (".md", ".csv")):
        rel = f"raw/{path.relative_to(root / 'raw')}"
        index[rel] = _tag_for(rel, path, keys, urls, manifest.get(rel))
    bundle._raw_products = index  # type: ignore[attr-defined]
    return index


def _tag_for(
    rel: str,
    path: Path,
    keys: dict[str, set[str]],
    urls: dict[str, str],
    row: dict[str, Any] | None,
) -> str:
    if not keys:
        return UNKNOWN
    stem = path.stem.lower()
    if rel.startswith("raw/benefit-tables/"):
        return stem if stem in keys else UNKNOWN
    if rel.startswith("raw/web/"):
        if row is not None:
            for candidate in (row.get("canonical"), row.get("url")):
                if candidate and _norm_url(str(candidate)) in urls:
                    return urls[_norm_url(str(candidate))]
            url = str(row.get("url") or "")
            if _SHARED_WEB.search(url):
                return SHARED
            if str(row.get("page_type") or "") == "product":
                hit = _longest(url.lower(), keys)
                if hit:
                    return hit
        if _SHARED_WEB.search(rel):
            return SHARED
        return UNKNOWN
    hit = _longest(stem, keys)
    return hit or UNKNOWN


def _longest(haystack: str, keys: dict[str, set[str]]) -> str | None:
    """The product whose longest form appears in the text; None if none does.

    Longest wins so that `travel-infinite` beats `travel` inside
    `travel-infinite-policy-wording`, and a tie on length goes to no one
    rather than to the alphabet.
    """
    flat = "".join(_WORD.findall(haystack))
    best: tuple[int, str] | None = None
    tied = False
    for slug, forms in keys.items():
        for form in forms:
            needle = "".join(_WORD.findall(form))
            if needle and needle in flat:
                if best is None or len(needle) > best[0]:
                    best, tied = (len(needle), slug), False
                elif len(needle) == best[0] and slug != best[1]:
                    tied = True
    if best is None or tied:
        return None
    return best[1]


@dataclass(frozen=True)
class Scope:
    """What this turn may read. `product` None means everything."""

    product: str | None = None

    @classmethod
    def open(cls) -> Scope:
        return cls(None)

    @classmethod
    def for_product(cls, product: str | None) -> Scope:
        return cls(product or None)

    @property
    def scoped(self) -> bool:
        return self.product is not None

    def allows_page(self, bundle: Bundle, page: Page) -> bool:
        """A product's own pages, plus everything that belongs to no product.

        Concepts, channels, journeys and the entity page are shared by every
        product and are what "what is an excess" is answered from; excluding
        them would make a scoped turn unable to define its own terms.
        """
        if not self.scoped or not page.id.startswith("product/"):
            return True
        return bundle.product_key(page) == self.product

    def allows_raw(self, bundle: Bundle, rel: str) -> bool:
        if not self.scoped:
            return True
        index = raw_product_index(bundle)
        if not any(tag not in (UNKNOWN,) for tag in index.values()):
            # Nothing is tagged — no catalogue. A filter that cannot tell
            # products apart admits rather than drops.
            return True
        tag = index.get(rel, UNKNOWN)
        return tag == self.product or tag == SHARED

    def describe(self) -> str:
        return self.product or "open"
