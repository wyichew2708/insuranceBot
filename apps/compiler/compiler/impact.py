"""Impact analysis (§D.1).

A change to a travel policy wording touches the travel benefit and exclusion
pages and the travel claim journey — recompile exactly those, not the whole
bundle. Token cost of a full recompile over this corpus is not academic.
"""

from __future__ import annotations

from okf.linter import SOURCE_REF_RE

from okf import Bundle


def pages_citing(bundle: Bundle, source_path: str) -> list[str]:
    """Wiki pages whose body or authority list references a raw source."""
    hits: list[str] = []
    for page in bundle.pages.values():
        cited = {m.group(1) for m in SOURCE_REF_RE.finditer(page.body)}
        if source_path in cited or source_path in page.frontmatter.authority:
            hits.append(page.id)
    return sorted(hits)


def impact_set(bundle: Bundle, changed_sources: list[str]) -> dict[str, list[str]]:
    return {source: pages_citing(bundle, source) for source in changed_sources}


def recompile_queue(bundle: Bundle, changed_sources: list[str]) -> list[str]:
    queue: set[str] = set()
    for pages in impact_set(bundle, changed_sources).values():
        queue.update(pages)
    return sorted(queue)
