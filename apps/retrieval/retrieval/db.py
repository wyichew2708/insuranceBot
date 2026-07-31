"""Postgres access for the retrieval service (psycopg3, pgvector)."""

from __future__ import annotations

import json
from typing import Any

import psycopg
from contracts.api import SearchFilters, SearchIndex, SearchResult
from psycopg.rows import dict_row

from retrieval.search import build_filter_sql, rrf_fuse, sparse_overlap_score


async def connect(database_url: str) -> psycopg.AsyncConnection[Any]:
    dsn = database_url.replace("postgresql+psycopg://", "postgresql://")
    return await psycopg.AsyncConnection.connect(dsn, row_factory=dict_row)


async def hybrid_search(
    conn: psycopg.AsyncConnection[Any],
    index: SearchIndex,
    filters: SearchFilters,
    query_dense: list[float],
    query_sparse: dict[str, float],
    candidate_pool: int = 30,
) -> list[SearchResult]:
    """Dense (pgvector cosine) + sparse (lexical overlap) -> RRF. The caller
    reranks the fused pool via the rerank endpoint and cuts to top_k."""
    table = "kb_chunks" if index == SearchIndex.kb else "web_chunks"
    sql_filter = build_filter_sql(index, filters)
    dense_sql = (
        f"SELECT chunk_id, text, metadata, sparse, dense <=> %(qvec)s::vector AS dist "
        f"FROM {table} WHERE {sql_filter.where} ORDER BY dist ASC LIMIT %(pool)s"
    )
    params = {**sql_filter.params, "qvec": json.dumps(query_dense), "pool": candidate_pool}
    async with conn.cursor() as cur:
        await cur.execute(dense_sql.encode(), params)  # type: ignore[arg-type]
        rows = await cur.fetchall()

    by_id = {row["chunk_id"]: row for row in rows}
    dense_rank = [row["chunk_id"] for row in rows]
    sparse_scored = sorted(
        rows,
        key=lambda row: -sparse_overlap_score(query_sparse, row.get("sparse") or {}),
    )
    sparse_rank = [row["chunk_id"] for row in sparse_scored]

    fused = rrf_fuse([dense_rank, sparse_rank])
    return [
        SearchResult(
            chunk_id=chunk_id,
            text=by_id[chunk_id]["text"],
            score=score,
            metadata=by_id[chunk_id].get("metadata") or {},
        )
        for chunk_id, score in fused
    ]
