"""Langfuse wiring: one trace per chat turn, spans per LLM call / tool / grader.

Falls back to a structured-logging no-op when Langfuse is not configured, so
services and tests never hard-depend on the SDK being importable or reachable.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

logger = logging.getLogger("observability")


class Tracer:
    """Minimal trace/span facade. Wraps Langfuse when configured."""

    def __init__(self, host: str = "", public_key: str = "", secret_key: str = "") -> None:
        self._langfuse: Any = None
        if host and public_key and secret_key:
            try:
                from langfuse import Langfuse

                self._langfuse = Langfuse(host=host, public_key=public_key, secret_key=secret_key)
            except ImportError:  # pragma: no cover - optional dependency
                logger.warning("langfuse configured but SDK not installed; tracing to logs only")

    def new_trace_id(self) -> str:
        return uuid.uuid4().hex

    @contextmanager
    def span(self, trace_id: str, name: str, **attrs: Any) -> Iterator[dict[str, Any]]:
        start = time.monotonic()
        record: dict[str, Any] = {"trace_id": trace_id, "name": name, **attrs}
        try:
            yield record
        finally:
            record["duration_ms"] = round((time.monotonic() - start) * 1000, 1)
            logger.info("span %s", record)
            if self._langfuse is not None:  # pragma: no cover - needs live langfuse
                try:
                    self._langfuse.span(trace_id=trace_id, name=name, metadata=record)
                except Exception:
                    logger.exception("langfuse span emit failed")


_tracer: Tracer | None = None


def get_tracer() -> Tracer:
    global _tracer
    if _tracer is None:
        from contracts.settings import get_settings

        s = get_settings()
        _tracer = Tracer(s.langfuse_host, s.langfuse_public_key, s.langfuse_secret_key)
    return _tracer
