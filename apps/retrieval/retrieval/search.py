"""Hybrid search core (§6.2).

Mandatory filters are applied IN SQL before any scoring — hard product rule 6
(`audience: internal` never retrievable in public sessions) lives here, not in
a prompt. The pure functions (filter SQL builder, RRF) are unit-tested; the
DB executor is exercised in integration tests against pgvector.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any

from contracts.api import SearchFilters, SearchIndex
from contracts.okf import Audience

# Which audiences a session may see, by session audience.
AUDIENCE_ALLOWED: dict[Audience, list[str]] = {
    Audience.public: ["public"],
    Audience.policyholder: ["public", "policyholder"],
    Audience.internal: ["public", "policyholder", "internal"],
}


@dataclass
class SqlQuery:
    where: str
    params: dict[str, Any]


def build_kb_filter_sql(filters: SearchFilters) -> SqlQuery:
    """WHERE clause for kb_chunks. Every condition is mandatory (§6.2.2)."""
    allowed = AUDIENCE_ALLOWED[filters.audience]
    params: dict[str, Any] = {
        "audiences": allowed,
        "brand": filters.brand.value,
        "language": filters.language.value,
        "jurisdiction": filters.jurisdiction.value,
        "today": filters.active_on or dt.date.today(),
    }
    conditions = [
        "active = true",
        "metadata->>'status' = 'published'",
        # belt-and-braces: explicit inequality plus allowed set
        "metadata->>'audience' != 'internal'" if filters.audience != Audience.internal else "TRUE",
        "metadata->>'audience' = ANY(%(audiences)s)",
        "metadata->'brand' ? %(brand)s",
        "metadata->>'language' = %(language)s",
        "metadata->>'jurisdiction' = %(jurisdiction)s",
        "(metadata->>'effective_from')::date <= %(today)s",
        "COALESCE((metadata->>'effective_to')::date, 'infinity'::date) > %(today)s",
    ]
    if filters.line:
        conditions.append("metadata->>'line' IN (%(line)s, 'common')")
        params["line"] = filters.line
    if filters.product_code:
        conditions.append("metadata->>'product_code' IN (%(product_code)s, 'ALL')")
        params["product_code"] = filters.product_code
    return SqlQuery(where=" AND ".join(conditions), params=params)


def build_web_filter_sql(filters: SearchFilters) -> SqlQuery:
    """WHERE clause for web_chunks: brand + TTL; expired promos never surface."""
    params: dict[str, Any] = {"brand": filters.brand.value, "now": dt.datetime.now(dt.UTC)}
    conditions = [
        "brand = %(brand)s",
        "expires_at > %(now)s",
        "demoted = false",
    ]
    return SqlQuery(where=" AND ".join(conditions), params=params)


def build_filter_sql(index: SearchIndex, filters: SearchFilters) -> SqlQuery:
    if index == SearchIndex.kb:
        return build_kb_filter_sql(filters)
    return build_web_filter_sql(filters)


def rrf_fuse(rank_lists: list[list[str]], k: int = 60) -> list[tuple[str, float]]:
    """Reciprocal Rank Fusion over id lists (dense ranks + sparse ranks)."""
    scores: dict[str, float] = {}
    for ranks in rank_lists:
        for position, chunk_id in enumerate(ranks):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + position + 1)
    return sorted(scores.items(), key=lambda item: (-item[1], item[0]))


def sparse_overlap_score(query_weights: dict[str, float], doc_weights: dict[str, float]) -> float:
    """BGE-M3 lexical-weight dot product for in-process sparse scoring."""
    if not query_weights or not doc_weights:
        return 0.0
    return sum(w * doc_weights.get(token, 0.0) for token, w in query_weights.items())
