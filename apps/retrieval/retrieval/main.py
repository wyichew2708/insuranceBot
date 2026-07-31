"""Retrieval service endpoints (§4.3, §6.2)."""

from __future__ import annotations

from typing import Any

import psycopg
from contracts.api import CompareRequest, SearchRequest, SearchResult
from contracts.settings import get_settings
from fastapi import FastAPI, HTTPException
from insurance_clients.vllm import VllmClient, VllmEndpoint
from psycopg.rows import dict_row

from retrieval.db import connect, hybrid_search

app = FastAPI(title="retrieval")


async def _conn() -> psycopg.AsyncConnection[Any]:
    settings = get_settings()
    if not settings.database_url:
        raise HTTPException(status_code=503, detail="DATABASE_URL not configured")
    try:
        return await connect(settings.database_url)
    except psycopg.OperationalError as exc:
        raise HTTPException(status_code=503, detail=f"database unavailable: {exc}") from exc


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/readyz")
async def readyz() -> dict[str, str]:
    conn = await _conn()
    await conn.close()
    return {"status": "ready"}


@app.post("/search")
async def search(req: SearchRequest) -> list[SearchResult]:
    settings = get_settings()
    embed_client = VllmClient(
        VllmEndpoint(
            base_url=settings.vllm_embed_base_url,
            model=settings.vllm_embed_model,
            api_key=settings.vllm_api_key,
        )
    )
    conn = await _conn()
    try:
        [embedding] = await embed_client.embed([req.query])
        candidates = await hybrid_search(conn, req.index, req.filters, embedding.dense, embedding.sparse)
        if not candidates:
            return []
        rerank_client = VllmClient(
            VllmEndpoint(
                base_url=settings.vllm_rerank_base_url,
                model=settings.vllm_rerank_model,
                api_key=settings.vllm_api_key,
            )
        )
        try:
            scores = await rerank_client.rerank(req.query, [c.text for c in candidates])
            reranked = sorted(zip(candidates, scores, strict=True), key=lambda p: -p[1])
            return [
                SearchResult(chunk_id=c.chunk_id, text=c.text, score=s, metadata=c.metadata)
                for c, s in reranked[: req.top_k]
            ]
        finally:
            await rerank_client.aclose()
    finally:
        await embed_client.aclose()
        await conn.close()


@app.get("/page/{block_id:path}")
async def read_page(block_id: str) -> dict[str, Any]:
    conn = await _conn()
    try:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "SELECT block_id, string_agg(text, E'\\n\\n' ORDER BY chunk_id) AS text,"
                " (array_agg(metadata))[1] AS metadata"
                " FROM kb_chunks WHERE block_id = %s AND active = true GROUP BY block_id",
                (block_id,),
            )
            row = await cur.fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail=f"block {block_id!r} not found")
        return dict(row)
    finally:
        await conn.close()


@app.get("/catalogue/{product_code}")
async def get_catalogue(product_code: str) -> dict[str, Any]:
    conn = await _conn()
    try:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "SELECT product_code, brand, line, name, data FROM catalogue_products"
                " WHERE product_code = %s",
                (product_code,),
            )
            row = await cur.fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail=f"product {product_code!r} not found")
        return dict(row)
    finally:
        await conn.close()


@app.post("/catalogue/compare")
async def compare(req: CompareRequest) -> dict[str, Any]:
    conn = await _conn()
    try:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "SELECT product_code, name, data FROM catalogue_products WHERE product_code = ANY(%s)",
                (req.product_codes,),
            )
            rows = await cur.fetchall()
        found = {row["product_code"]: row for row in rows}
        missing = [p for p in req.product_codes if p not in found]
        if missing:
            raise HTTPException(status_code=404, detail=f"unknown products: {missing}")
        benefit_codes = req.benefit_codes or sorted(
            {b for row in rows for b in (row["data"].get("benefits") or {})}
        )
        table = [
            {
                "benefit_code": code,
                "values": {p: (found[p]["data"].get("benefits") or {}).get(code) for p in req.product_codes},
            }
            for code in benefit_codes
        ]
        return {"products": req.product_codes, "rows": table}
    finally:
        await conn.close()


@app.get("/actions/{brand}")
async def list_actions(brand: str) -> list[dict[str, Any]]:
    conn = await _conn()
    try:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "SELECT action_id, brand, kind, value, label, verbatim FROM actions WHERE brand = %s",
                (brand,),
            )
            return [dict(r) for r in await cur.fetchall()]
    finally:
        await conn.close()


@app.get("/actions/{brand}/{action_id}")
async def get_action(brand: str, action_id: str) -> dict[str, Any]:
    conn = await _conn()
    try:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "SELECT action_id, brand, kind, value, label, verbatim FROM actions"
                " WHERE brand = %s AND action_id = %s",
                (brand, action_id),
            )
            row = await cur.fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail=f"action {action_id!r} not found for {brand}")
        return dict(row)
    finally:
        await conn.close()
