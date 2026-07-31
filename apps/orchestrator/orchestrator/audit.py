"""Audit trail (§4.2 audit_log): every tool call + verification verdict.

Entries are collected synchronously during the turn and flushed to Postgres
best-effort at the end; without a DATABASE_URL they land in structured logs
only (dev / unit tests).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("orchestrator.audit")


@dataclass
class Auditor:
    session_id: str
    database_url: str = ""
    entries: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    def emit(self, event: str, payload: dict[str, Any]) -> None:
        self.entries.append((event, payload))
        logger.info("audit session=%s event=%s payload=%s", self.session_id, event, payload)

    async def flush(self) -> None:
        if not self.database_url or not self.entries:
            return
        try:
            import psycopg

            dsn = self.database_url.replace("postgresql+psycopg://", "postgresql://")
            async with await psycopg.AsyncConnection.connect(dsn) as conn:
                async with conn.cursor() as cur:
                    for event, payload in self.entries:
                        await cur.execute(
                            "INSERT INTO audit_log (session_id, event, payload) VALUES (%s, %s, %s)",
                            (self.session_id, event, json.dumps(payload, default=str)),
                        )
                await conn.commit()
        except Exception as exc:  # audit persistence must never break a turn
            logger.warning("audit flush failed: %s", exc)
