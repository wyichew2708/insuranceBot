"""Web-index writer (§7.6): chunk page text, embed, upsert web_chunks.

Chunking is word-window based (web pages have no OKF structure); each chunk
carries the page's freshness metadata so retrieval and the promo-freshness
grader can filter on it.
"""

from __future__ import annotations

import json
from typing import Any

import psycopg
from contracts.settings import Settings
from insurance_clients.vllm import VllmClient, VllmEndpoint

from crawler.worker import CrawledPage

MAX_CHUNK_WORDS = 350
OVERLAP_WORDS = 40


def chunk_page_text(text: str, max_words: int = MAX_CHUNK_WORDS, overlap: int = OVERLAP_WORDS) -> list[str]:
    words = text.split()
    if not words:
        return []
    if len(words) <= max_words:
        return [" ".join(words)]
    chunks = []
    start = 0
    while start < len(words):
        chunks.append(" ".join(words[start : start + max_words]))
        if start + max_words >= len(words):
            break
        start += max_words - overlap
    return chunks


def page_metadata(page: CrawledPage) -> dict[str, Any]:
    return {
        "page_type": page.page_type,
        "url": page.url,
        "canonical_url": page.canonical_url,
        "brand": page.brand,
        "expires_at": page.expires_at.isoformat(),
        "accurate_as_of": page.accurate_as_of.isoformat() if page.accurate_as_of else None,
        "demoted": page.demoted,
    }


async def index_page(conn: psycopg.AsyncConnection[Any], page: CrawledPage, embedder: VllmClient) -> int:
    """Replace all chunks for the page's canonical URL. Returns chunk count."""
    texts = chunk_page_text(page.text)
    embeddings = await embedder.embed(texts) if texts else []
    metadata = page_metadata(page)
    async with conn.cursor() as cur:
        await cur.execute("DELETE FROM web_chunks WHERE canonical_url = %s", (page.canonical_url,))
        for i, (text, emb) in enumerate(zip(texts, embeddings, strict=True)):
            await cur.execute(
                "INSERT INTO web_chunks (chunk_id, url, canonical_url, brand, text, dense, sparse,"
                " fetched_at, expires_at, accurate_as_of, page_type, demoted, metadata)"
                " VALUES (%s, %s, %s, %s, %s, %s::vector, %s, %s, %s, %s, %s, %s, %s)",
                (
                    f"{page.canonical_url}#{i}",
                    page.url,
                    page.canonical_url,
                    page.brand,
                    text,
                    json.dumps(emb.dense),
                    json.dumps(emb.sparse),
                    page.fetched_at,
                    page.expires_at,
                    page.accurate_as_of,
                    page.page_type,
                    page.demoted,
                    json.dumps(metadata),
                ),
            )
    await conn.commit()
    return len(texts)


def make_embedder(settings: Settings) -> VllmClient:
    return VllmClient(
        VllmEndpoint(
            base_url=settings.vllm_embed_base_url,
            model=settings.vllm_embed_model,
            api_key=settings.vllm_api_key,
        )
    )
