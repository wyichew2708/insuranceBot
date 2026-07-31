"""Best-effort persistence for sessions, messages, feedback (§4.2).

Without DATABASE_URL everything degrades to structured logs — persistence
must never break or slow a chat turn.
"""

from __future__ import annotations

import logging

logger = logging.getLogger("gateway.storage")


async def store_message(
    database_url: str,
    session_id: str,
    channel: str,
    brand: str,
    audience: str,
    role: str,
    content: str,
    redacted_content: str,
) -> None:
    if not database_url:
        return
    try:
        import psycopg

        dsn = database_url.replace("postgresql+psycopg://", "postgresql://")
        async with await psycopg.AsyncConnection.connect(dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "INSERT INTO sessions (session_id, channel, brand, audience)"
                    " VALUES (%s, %s, %s, %s) ON CONFLICT (session_id) DO NOTHING",
                    (session_id, channel, brand, audience),
                )
                # NOTE: raw content is stored for audit; column-level encryption
                # (pgcrypto) is enabled in the Phase 4 hardening migration.
                await cur.execute(
                    "INSERT INTO messages (session_id, role, content, redacted_content)"
                    " VALUES (%s, %s, %s, %s)",
                    (session_id, role, content, redacted_content),
                )
            await conn.commit()
    except Exception as exc:
        logger.warning("message persistence failed: %s", exc)


async def store_feedback(database_url: str, session_id: str, rating: int, comment: str | None = None) -> bool:
    if not database_url:
        logger.info("feedback (no db): session=%s rating=%s", session_id, rating)
        return False
    try:
        import psycopg

        dsn = database_url.replace("postgresql+psycopg://", "postgresql://")
        async with await psycopg.AsyncConnection.connect(dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "INSERT INTO feedback (session_id, rating, comment) VALUES (%s, %s, %s)",
                    (session_id, rating, comment),
                )
            await conn.commit()
        return True
    except Exception as exc:
        logger.warning("feedback persistence failed: %s", exc)
        return False
