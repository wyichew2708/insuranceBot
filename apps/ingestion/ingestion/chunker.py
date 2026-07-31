"""Chunking rule (§4.1): one chunk per block; if block > 700 tokens, one chunk
per `##` section with chunk_id = {block_id}#{section-slug}. Never split
mid-section. Embedding text = title + section heading + text."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from contracts.okf import OkfBlock

MAX_BLOCK_TOKENS = 700


@dataclass
class Chunk:
    chunk_id: str
    block_id: str
    text: str
    embed_text: str
    metadata: dict[str, Any]


def estimate_tokens(text: str) -> int:
    """Cheap whitespace-based token estimate; deliberately conservative."""
    return len(text.split())


def slugify(heading: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", heading.lower()).strip("-")
    return slug or "section"


_SECTION_RE = re.compile(r"^## +(.+)$", re.MULTILINE)


def split_sections(body: str) -> list[tuple[str, str]]:
    """Returns [(heading, section_text)]; a leading un-headed part gets heading ''."""
    matches = list(_SECTION_RE.finditer(body))
    if not matches:
        return [("", body.strip())] if body.strip() else []
    sections: list[tuple[str, str]] = []
    lead = body[: matches[0].start()].strip()
    if lead:
        sections.append(("", lead))
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        sections.append((m.group(1).strip(), body[m.start() : end].strip()))
    return sections


def block_metadata(block: OkfBlock) -> dict[str, Any]:
    fm = block.frontmatter
    return {
        "type": fm.type.value,
        "title": fm.title,
        "product_code": fm.product_code,
        "line": fm.line,
        "audience": fm.audience.value,
        "brand": [b.value for b in fm.brand],
        "language": fm.language.value,
        "jurisdiction": fm.jurisdiction.value,
        "version": fm.version,
        "status": fm.status.value,
        "effective_from": fm.effective_from.isoformat(),
        "effective_to": fm.effective_to.isoformat() if fm.effective_to else None,
        "tags": fm.tags,
    }


def chunk_block(block: OkfBlock) -> list[Chunk]:
    fm = block.frontmatter
    meta = block_metadata(block)
    body = block.body.strip()

    if estimate_tokens(body) <= MAX_BLOCK_TOKENS:
        return [
            Chunk(
                chunk_id=fm.id,
                block_id=fm.id,
                text=body,
                embed_text=f"{fm.title}\n\n{body}",
                metadata=meta,
            )
        ]

    chunks: list[Chunk] = []
    seen_slugs: set[str] = set()
    for heading, section_text in split_sections(body):
        slug = slugify(heading) if heading else "intro"
        # keep chunk ids unique when headings repeat
        base = slug
        n = 2
        while slug in seen_slugs:
            slug = f"{base}-{n}"
            n += 1
        seen_slugs.add(slug)
        chunks.append(
            Chunk(
                chunk_id=f"{fm.id}#{slug}",
                block_id=fm.id,
                text=section_text,
                embed_text=f"{fm.title}\n{heading}\n\n{section_text}".strip(),
                metadata=meta,
            )
        )
    return chunks
