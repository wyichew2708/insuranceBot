"""Ingestion pipeline (§6.1): load -> lint -> chunk -> embed -> write inactive
-> atomic swap. Rollback re-activates a previous bundle in one transaction."""

from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path
from typing import Any

import psycopg
from contracts.settings import Settings
from insurance_clients.vllm import VllmClient, VllmEndpoint

from ingestion.chunker import Chunk, chunk_block
from ingestion.loader import load_bundle
from ingestion.validator import LintReport, lint_bundle

logger = logging.getLogger("ingestion.pipeline")

KEEP_BUNDLES = 3


class IngestionError(Exception):
    pass


def _dsn(settings: Settings) -> str:
    return settings.database_url.replace("postgresql+psycopg://", "postgresql://")


Embedded = tuple[Chunk, list[float], dict[str, float]]


async def embed_chunks(chunks: list[Chunk], settings: Settings) -> list[Embedded]:
    if not settings.vllm_embed_base_url:
        from insurance_clients.pseudo import pseudo_embedding, pseudo_sparse

        logger.warning("no embed endpoint configured — using dev pseudo-embeddings")
        return [(c, pseudo_embedding(c.embed_text), pseudo_sparse(c.embed_text)) for c in chunks]
    client = VllmClient(
        VllmEndpoint(
            base_url=settings.vllm_embed_base_url,
            model=settings.vllm_embed_model,
            api_key=settings.vllm_api_key,
        )
    )
    try:
        out: list[tuple[Chunk, list[float], dict[str, float]]] = []
        batch = 32
        for i in range(0, len(chunks), batch):
            group = chunks[i : i + batch]
            embeddings = await client.embed([c.embed_text for c in group])
            out.extend((chunk, emb.dense, emb.sparse) for chunk, emb in zip(group, embeddings, strict=True))
        return out
    finally:
        await client.aclose()


async def ingest_bundle(bundle_dir: Path, settings: Settings, activate: bool = True) -> str:
    """Full pipeline over a local bundle checkout. Returns the new bundle_id."""
    blocks = load_bundle(bundle_dir)
    report: LintReport = lint_bundle(blocks)
    if not report.ok:
        raise IngestionError("bundle lint failed:\n" + "\n".join(report.violations))

    chunks = [c for b in blocks for c in chunk_block(b)]
    embedded = await embed_chunks(chunks, settings)
    bundle_id = uuid.uuid4().hex[:12]

    async with await psycopg.AsyncConnection.connect(_dsn(settings)) as conn:
        async with conn.cursor() as cur:
            for chunk, dense, sparse in embedded:
                await cur.execute(
                    "INSERT INTO kb_chunks (chunk_id, block_id, bundle_id, text, dense, sparse,"
                    " metadata, active) VALUES (%s, %s, %s, %s, %s::vector, %s, %s, false)"
                    " ON CONFLICT (chunk_id) DO UPDATE SET bundle_id = EXCLUDED.bundle_id,"
                    " text = EXCLUDED.text, dense = EXCLUDED.dense, sparse = EXCLUDED.sparse,"
                    " metadata = EXCLUDED.metadata, active = false",
                    (
                        chunk.chunk_id,
                        chunk.block_id,
                        bundle_id,
                        chunk.text,
                        json.dumps(dense),
                        json.dumps(sparse),
                        json.dumps(chunk.metadata),
                    ),
                )
            await _load_catalogue(cur, bundle_dir, bundle_id, {b.frontmatter.id for b in blocks})
            await _load_actions(cur, bundle_dir)
        await conn.commit()

    if activate:
        await activate_bundle(bundle_id, settings)
    logger.info("ingested bundle %s: %d blocks, %d chunks", bundle_id, len(blocks), len(chunks))
    return bundle_id


async def _load_catalogue(
    cur: psycopg.AsyncCursor[Any], bundle_dir: Path, bundle_id: str, block_ids: set[str]
) -> None:
    path = bundle_dir / "catalogue" / "products.json"
    if not path.exists():
        return
    products = json.loads(path.read_text())
    for product in products:
        for ref in product.get("block_refs", []):
            if ref not in block_ids:
                raise IngestionError(f"catalogue {product['product_code']}: block_ref {ref!r} unresolved")
        await cur.execute(
            "INSERT INTO catalogue_products (product_code, brand, line, name, data, bundle_id)"
            " VALUES (%s, %s, %s, %s, %s, %s)"
            " ON CONFLICT (product_code) DO UPDATE SET brand = EXCLUDED.brand, line = EXCLUDED.line,"
            " name = EXCLUDED.name, data = EXCLUDED.data, bundle_id = EXCLUDED.bundle_id",
            (
                product["product_code"],
                product["brand"],
                product["line"],
                product["name"],
                json.dumps(product),
                bundle_id,
            ),
        )


async def _load_actions(cur: psycopg.AsyncCursor[Any], bundle_dir: Path) -> None:
    path = bundle_dir / "actions.json"
    if not path.exists():
        return
    actions = json.loads(path.read_text())
    for action in actions:
        await cur.execute(
            "INSERT INTO actions (action_id, brand, kind, value, label, verbatim)"
            " VALUES (%s, %s, %s, %s, %s, %s)"
            " ON CONFLICT (action_id, brand) DO UPDATE SET kind = EXCLUDED.kind,"
            " value = EXCLUDED.value, label = EXCLUDED.label, verbatim = EXCLUDED.verbatim",
            (
                action["action_id"],
                action["brand"],
                action["kind"],
                action["value"],
                action["label"],
                action.get("verbatim", False),
            ),
        )


async def activate_bundle(bundle_id: str, settings: Settings) -> None:
    """Atomic swap: one transaction flips active flags; keeps last KEEP_BUNDLES."""
    async with await psycopg.AsyncConnection.connect(_dsn(settings)) as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT 1 FROM kb_chunks WHERE bundle_id = %s LIMIT 1", (bundle_id,))
            if await cur.fetchone() is None:
                raise IngestionError(f"bundle {bundle_id!r} has no chunks; refusing to activate")
            await cur.execute("UPDATE kb_chunks SET active = (bundle_id = %s)", (bundle_id,))
            await cur.execute(
                "DELETE FROM kb_chunks WHERE bundle_id NOT IN ("
                " SELECT DISTINCT bundle_id FROM kb_chunks ORDER BY bundle_id DESC LIMIT %s)"
                " AND active = false",
                (KEEP_BUNDLES,),
            )
        await conn.commit()
    logger.info("activated bundle %s", bundle_id)


async def rollback(bundle_id: str, settings: Settings) -> None:
    """Re-activate a previously ingested bundle (kept among the last 3)."""
    await activate_bundle(bundle_id, settings)
    logger.info("rolled back to bundle %s", bundle_id)
