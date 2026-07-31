"""Weekly gap report (§10.5): cluster low-rated + unanswered questions and
propose the nearest existing KB block, so the CMS team sees what content is
missing. Output: CSV `question_cluster, count, nearest_block, suggested_block_type`.

Run: python -m analytics.gap_report [--days 7] [--k 8] [--out gap_report.csv]
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import logging
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from contracts.settings import Settings, get_settings
from insurance_clients.pseudo import pseudo_embedding

from analytics.kmeans import Vector, cosine, kmeans

logger = logging.getLogger("analytics.gap_report")

# Answers containing these markers signal an unanswered/degraded turn.
UNANSWERED_MARKERS = [
    "couldn't verify a reliable answer",
    "could you tell me a bit more",
    "having trouble answering right now",
]


@dataclass
class GapRow:
    question_cluster: str
    count: int
    nearest_block: str
    suggested_block_type: str


async def fetch_problem_questions(settings: Settings, days: int) -> list[str]:
    """Low-rated turns + turns whose reply matched an unanswered marker."""
    import psycopg
    from psycopg.rows import dict_row

    dsn = settings.database_url.replace("postgresql+psycopg://", "postgresql://")
    questions: list[str] = []
    async with (
        await psycopg.AsyncConnection.connect(dsn, row_factory=dict_row) as conn,
        conn.cursor() as cur,
    ):
        await cur.execute(
            "SELECT m.redacted_content AS q FROM feedback f"
            " JOIN messages m ON m.session_id = f.session_id AND m.role = 'user'"
            " WHERE f.rating < 0 AND f.created_at > now() - make_interval(days => %s)",
            (days,),
        )
        questions += [r["q"] for r in await cur.fetchall()]
        marker_clause = " OR ".join(["a.redacted_content ILIKE %s"] * len(UNANSWERED_MARKERS))
        await cur.execute(
            "SELECT u.redacted_content AS q FROM messages a"
            " JOIN messages u ON u.session_id = a.session_id AND u.role = 'user' AND u.id < a.id"
            f" WHERE a.role = 'assistant' AND ({marker_clause})"
            " AND a.created_at > now() - make_interval(days => %s)",
            (*(f"%{m}%" for m in UNANSWERED_MARKERS), days),
        )
        questions += [r["q"] for r in await cur.fetchall()]
    return [q for q in questions if q and q.strip()]


async def fetch_active_blocks(settings: Settings) -> list[dict[str, Any]]:
    import psycopg
    from psycopg.rows import dict_row

    dsn = settings.database_url.replace("postgresql+psycopg://", "postgresql://")
    async with (
        await psycopg.AsyncConnection.connect(dsn, row_factory=dict_row) as conn,
        conn.cursor() as cur,
    ):
        await cur.execute(
            "SELECT DISTINCT block_id, metadata->>'title' AS title, metadata->>'type' AS type,"
            " text FROM kb_chunks WHERE active = true"
        )
        return [dict(r) for r in await cur.fetchall()]


SUGGESTED_TYPE_BY_KEYWORD = [
    (["how do i", "how to", "where do i", "submit", "cancel", "update", "change"], "procedure"),
    (["cover", "covered", "benefit", "limit", "reimburse"], "benefit"),
    (["excluded", "exclusion", "not covered"], "exclusion"),
    (["eligible", "who can", "requirement"], "eligibility"),
]


def suggest_block_type(question: str) -> str:
    q = question.lower()
    for keywords, block_type in SUGGESTED_TYPE_BY_KEYWORD:
        if any(kw in q for kw in keywords):
            return block_type
    return "faq"


def build_report(
    questions: list[str],
    blocks: list[dict[str, Any]],
    k: int,
    embed: Any = pseudo_embedding,
) -> list[GapRow]:
    if not questions:
        return []
    vectors: list[Vector] = [embed(q) for q in questions]
    labels = kmeans(vectors, k)
    block_vectors = [(b, embed(f"{b.get('title') or ''} {b['text']}")) for b in blocks]

    rows: list[GapRow] = []
    for cluster_id, count in Counter(labels).most_common():
        members = [q for q, label in zip(questions, labels, strict=True) if label == cluster_id]
        member_vectors = [v for v, label in zip(vectors, labels, strict=True) if label == cluster_id]
        centroid = [sum(col) / len(member_vectors) for col in zip(*member_vectors, strict=True)]
        representative = min(members, key=len)
        nearest = ""
        if block_vectors:
            nearest = max(block_vectors, key=lambda bv: cosine(centroid, bv[1]))[0]["block_id"]
        rows.append(
            GapRow(
                question_cluster=representative,
                count=count,
                nearest_block=nearest,
                suggested_block_type=suggest_block_type(representative),
            )
        )
    return rows


def write_csv(rows: list[GapRow], path: Path) -> None:
    with path.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["question_cluster", "count", "nearest_block", "suggested_block_type"])
        for row in rows:
            writer.writerow([row.question_cluster, row.count, row.nearest_block, row.suggested_block_type])


async def run(days: int, k: int, out: Path) -> int:
    settings = get_settings()
    if not settings.database_url:
        logger.error("DATABASE_URL required for the gap report")
        return 0
    questions = await fetch_problem_questions(settings, days)
    blocks = await fetch_active_blocks(settings)
    rows = build_report(questions, blocks, k)
    write_csv(rows, out)
    logger.info("gap report: %d clusters from %d questions -> %s", len(rows), len(questions), out)
    return len(rows)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--k", type=int, default=8)
    parser.add_argument("--out", type=Path, default=Path("gap_report.csv"))
    args = parser.parse_args()
    asyncio.run(run(args.days, args.k, args.out))


if __name__ == "__main__":
    main()
