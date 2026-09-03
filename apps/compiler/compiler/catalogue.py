"""The product catalogue: which pages are products, by the owner's say-so.

The crawl labels a page "product" from its URL, and on this corpus that made
156 products out of a catalogue of thirty-eight: every business sub-category
stub, four life-stage landing pages under Term Life, a webinar, a fire-safety
event, a survey form. A customer who typed "tiq home" was asked whether they
meant the event or the webinar.

`catalogue.yaml` at the bundle root lists the products — name, official
pages, other names, and the name keys that identify each product's policy
documents. When it is present, it is the only authority on what a product
is: a crawled page it does not list is not compiled as a product, and a
document that matches no entry is reported rather than compiled. When it is
absent, the compiler falls back to the crawl's labels, as before.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

import yaml

CATALOGUE_FILE = "catalogue.yaml"


@dataclass(frozen=True)
class Entry:
    slug: str
    name: str
    brand: str = ""
    category: str = ""
    #: `active` | `legacy` — legacy is closed to new business, still answered.
    status: str = "active"
    urls: tuple[str, ...] = ()
    aliases: tuple[str, ...] = ()
    #: Name keys matched against a document's plan name (`normalise_plan`).
    documents: tuple[str, ...] = ()

    @property
    def legacy(self) -> bool:
        return self.status == "legacy"

    @property
    def names(self) -> tuple[str, ...]:
        return (self.name, *self.aliases)


@dataclass
class Catalogue:
    entries: list[Entry] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._by_url: dict[str, list[Entry]] = {}
        for entry in self.entries:
            for url in entry.urls:
                self._by_url.setdefault(normalise_url(url), []).append(entry)

    def entries_for_url(self, url: str) -> list[Entry]:
        return self._by_url.get(normalise_url(url), [])

    def entry_for_url(self, url: str) -> Entry | None:
        """The one entry a page belongs to. A page several entries share —
        the Life & Critical Illness category page — belongs to none: it
        describes the category, and it would become what each plan *is*."""
        found = self.entries_for_url(url)
        return found[0] if len(found) == 1 else None

    def entries_for_document(self, plan: str) -> list[Entry]:
        """Every entry whose document keys, slug, or a slugified alias sits
        inside the document's plan name. Exact-key containment: `tiq-home`
        inside `tiq-home-policy-wording`'s plan `tiq-home`, but `home` alone
        does not claim `home-renewal-protection-bundle`."""
        haystack = f"-{plan.lower()}-"
        found: list[Entry] = []
        for entry in self.entries:
            keys = {entry.slug, *entry.documents, *(slugify_name(a) for a in entry.aliases)}
            if any((len(k) >= 4 and f"-{k}-" in haystack) or f"-{k}-" == haystack for k in keys):
                found.append(entry)
        return found

    @property
    def slugs(self) -> list[str]:
        return [e.slug for e in self.entries]

    def get(self, slug: str) -> Entry | None:
        return next((e for e in self.entries if e.slug == slug), None)


def normalise_url(url: str) -> str:
    parts = urlsplit(url.strip())
    host = (parts.hostname or "").lower().removeprefix("www.")
    path = re.sub(r"/+$", "", parts.path).lower() or "/"
    return f"{host}{path}"


def slugify_name(name: str) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", name.lower())).strip("-")


def load_catalogue(bundle_root: Path) -> Catalogue | None:
    path = bundle_root / CATALOGUE_FILE
    if not path.exists():
        return None
    data = yaml.safe_load(path.read_text()) or {}
    entries: list[Entry] = []
    for raw in data.get("products", []):
        entries.append(
            Entry(
                slug=str(raw["slug"]),
                name=str(raw["name"]),
                brand=str(raw.get("brand", "")),
                category=str(raw.get("category", "")),
                status=str(raw.get("status", "active")),
                urls=tuple(str(u) for u in raw.get("urls", [])),
                aliases=tuple(str(a) for a in raw.get("aliases", [])),
                documents=tuple(str(d) for d in raw.get("documents", [])),
            )
        )
    return Catalogue(entries)
