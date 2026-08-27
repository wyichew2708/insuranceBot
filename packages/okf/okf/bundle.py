"""Bundle loading, alias resolution and graph traversal (§C.1, §E.1).

The bundle is a git repo: `raw/` immutable sources, `wiki/` compiled pages.
Loading builds the frontmatter index the pre-read filter runs against, plus
the alias index — per §B.3 the cheapest single accuracy win in the build.
"""

from __future__ import annotations

import datetime as dt
import math
import re
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from okf.page import Page, PageType, Status, parse_page
from okf.tables import BenefitTables


@dataclass
class Manifest:
    name: str = "etiqa-sg-knowledge"
    okf_version: str = "0.1"
    jurisdiction: str = "SG"
    underwriter: str = ""
    uen: str = ""
    # True when the corpus is development or synthetic data. Everything
    # downstream that shows numbers to a human is expected to say so.
    fixture: bool = False

    @classmethod
    def load(cls, path: Path) -> Manifest:
        if not path.exists():
            return cls()
        data = yaml.safe_load(path.read_text()) or {}
        return cls(
            name=str(data.get("name", "etiqa-sg-knowledge")),
            okf_version=str(data.get("okf_version", "0.1")),
            jurisdiction=str(data.get("jurisdiction", "SG")),
            underwriter=str(data.get("underwriter", "")),
            uen=str(data.get("uen", "")),
            fixture=bool(data.get("fixture", False)),
        )


@dataclass
class Bundle:
    root: Path
    manifest: Manifest
    pages: dict[str, Page] = field(default_factory=dict)
    tables: BenefitTables = field(default_factory=lambda: BenefitTables([]))
    load_errors: list[str] = field(default_factory=list)
    _alias_index: dict[str, list[str]] = field(default_factory=dict, repr=False)
    _idf: dict[str, float] = field(default_factory=dict, repr=False)

    @classmethod
    def load(cls, root: Path) -> Bundle:
        root = Path(root)
        manifest = Manifest.load(root / "okf.yaml")
        pages: dict[str, Page] = {}
        errors: list[str] = []
        wiki = root / "wiki"
        for path in sorted(wiki.rglob("*.md")):
            try:
                page = parse_page(path.read_text(), source_path=str(path.relative_to(root)))
            except Exception as exc:
                errors.append(f"{path.relative_to(root)}: {exc}")
                continue
            if page.id in pages:
                errors.append(f"duplicate page id {page.id!r} ({path.relative_to(root)})")
                continue
            pages[page.id] = page
        tables_dir = root / "raw" / "benefit-tables"
        tables = BenefitTables.from_dir(tables_dir) if tables_dir.is_dir() else BenefitTables([])
        bundle = cls(root=root, manifest=manifest, pages=pages, tables=tables, load_errors=errors)
        bundle._build_alias_index()
        return bundle

    def _build_alias_index(self) -> None:
        index: dict[str, list[str]] = {}
        for page in self.pages.values():
            # Only the title and the authored alias list. Auto-indexing the
            # last id segment made the bare word "travel" an alias of every
            # page ending in /travel, so a generic mention boosted the journey
            # and product pages equally — aliases are authored, not inferred.
            keys = [page.frontmatter.title, *page.frontmatter.aliases]
            for key in keys:
                normalised = normalise(key)
                if not normalised:
                    continue
                index.setdefault(normalised, [])
                if page.id not in index[normalised]:
                    index[normalised].append(page.id)
        self._alias_index = index

    # --- lookup -----------------------------------------------------------

    def get(self, page_id: str) -> Page | None:
        return self.pages.get(page_id)

    def by_type(self, page_type: PageType) -> list[Page]:
        return [p for p in self.pages.values() if p.frontmatter.type == page_type]

    def resolve_aliases(self, text: str) -> list[str]:
        """Longest-alias-first entity resolution over the query text."""
        haystack = normalise(text)
        hits: list[tuple[int, str]] = []
        for alias, page_ids in self._alias_index.items():
            if alias and alias in haystack:
                for page_id in page_ids:
                    hits.append((len(alias), page_id))
        seen: set[str] = set()
        ordered: list[str] = []
        for _, page_id in sorted(hits, key=lambda pair: -pair[0]):
            if page_id not in seen:
                seen.add(page_id)
                ordered.append(page_id)
        return ordered

    def alias_fanout(self, text: str) -> dict[str, int]:
        """Page id → how many pages the alias that matched it also matches.

        An alias is only evidence to the extent it separates one page from the
        others. The compiler stamps every promotion page with "discount" and
        every journey page with "claim", so those two words each resolve to
        dozens of pages at once — a hit that says nothing about which. The
        caller weights the bonus by this, which is IDF wearing a different hat.

        Where several aliases matched one page, the sharpest one wins.
        """
        haystack = normalise(text)
        fanout: dict[str, int] = {}
        for alias, page_ids in self._alias_index.items():
            if not alias or alias not in haystack:
                continue
            for page_id in page_ids:
                current = fanout.get(page_id)
                if current is None or len(page_ids) < current:
                    fanout[page_id] = len(page_ids)
        return fanout

    def aliases_of(self, page_id: str) -> list[str]:
        page = self.pages.get(page_id)
        return list(page.frontmatter.aliases) if page else []

    # --- graph ------------------------------------------------------------

    def neighbours(self, page_id: str) -> list[str]:
        page = self.pages.get(page_id)
        if page is None:
            return []
        return [ref for ref in page.frontmatter.links.all_refs() if ref in self.pages]

    def traverse(self, start: str, max_pages: int = 6) -> list[str]:
        """Breadth-first link traversal — the deterministic replacement for
        multi-hop RAG (§E.1). Guarantees the complete exclusion set rather
        than hoping a retriever surfaces the exclusion chunk."""
        if start not in self.pages:
            return []
        order: list[str] = []
        seen = {start}
        queue: deque[str] = deque([start])
        while queue and len(order) < max_pages:
            current = queue.popleft()
            order.append(current)
            for neighbour in self.neighbours(current):
                if neighbour not in seen:
                    seen.add(neighbour)
                    queue.append(neighbour)
        return order

    def product_key(self, page: Page) -> str:
        """Benefit-table key for a page. Sub-pages inherit their parent
        product's key, so product/general/travel/benefits resolves to
        `travel` rather than `benefits`."""
        explicit = (page.frontmatter.model_extra or {}).get("product_key")
        if isinstance(explicit, str) and explicit:
            return explicit
        parts = page.id.split("/")
        for i in range(len(parts) - 1, 0, -1):
            ancestor = "/".join(parts[:i])
            if ancestor in self.pages:
                return ancestor.rsplit("/", 1)[-1]
        return parts[-1]

    def broken_links(self) -> list[tuple[str, str]]:
        broken: list[tuple[str, str]] = []
        for page in self.pages.values():
            for ref in page.frontmatter.links.all_refs():
                if ref not in self.pages:
                    broken.append((page.id, ref))
        return broken

    def retrievable(self, page: Page, today: dt.date) -> bool:
        """Wiki-first retrieval admits only approved, in-window, non-stale
        pages; everything else falls through to RAG over raw/ (§I staleness)."""
        fm = page.frontmatter
        return fm.status == Status.approved and fm.is_effective_on(today) and not fm.is_review_overdue(today)


def normalise(text: str) -> str:
    return " ".join("".join(ch.lower() if ch.isalnum() else " " for ch in text).split())


WORD_RE = re.compile(r"[a-z0-9]+")


def page_terms(page: Page) -> set[str]:
    fm = page.frontmatter
    text = " ".join([fm.title, " ".join(fm.aliases), fm.id.replace("/", " "), page.body])
    return {w for w in WORD_RE.findall(text.lower()) if len(w) > 2}


def term_idf(bundle: Bundle) -> dict[str, float]:
    """Inverse document frequency over the corpus, normalised to (0, 1].

    Without it, "insurance" and "cover" count as much as the word that actually
    identifies the product — so a question about a line the corpus does not
    carry ("what does your crop insurance cover?") scores like a real one and
    gets answered from whichever page happens to sort first. Weighting by
    rarity makes the absence of evidence visible as a low score, which is what
    the confidence floor and the RAG fallback are there to act on.
    """
    if not bundle._idf:
        total = max(len(bundle.pages), 1)
        frequency: dict[str, int] = {}
        for page in bundle.pages.values():
            for term in page_terms(page):
                frequency[term] = frequency.get(term, 0) + 1
        ceiling = math.log(1 + total)
        bundle._idf = {t: math.log(1 + total / c) / ceiling for t, c in frequency.items()}
    return bundle._idf
