"""Advice-boundary classifier shared by the pipeline and the gate (§F.2)."""

from __future__ import annotations

from harness.gates import ADVICE_SEEKING_RE

from okf import Bundle


def advice_required(bundle: Bundle, question: str, loaded_page_ids: list[str]) -> bool:
    if ADVICE_SEEKING_RE.search(question):
        return True
    return any(
        (page := bundle.get(page_id)) is not None and page.frontmatter.regulated_advice
        for page_id in loaded_page_ids
    )
