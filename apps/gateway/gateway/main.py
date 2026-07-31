"""Gateway: the only public entrypoint.

POST /v1/chat: rate-limit -> redact PII -> injection screen -> forward to
orchestrator -> stream SSE back. Audience/brand come from the session
issuance path (JWT in Phase 4); v1 accepts them in the request body but a
widget key -> brand binding will replace client-supplied brand.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator

import httpx
from contracts.api import ChatRequest
from contracts.settings import get_settings
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse

from gateway.rate_limit import MemoryRateLimiter, RateLimiter, build_rate_limiter
from gateway.redaction import redact, screen_injection

logger = logging.getLogger("gateway")

app = FastAPI(title="gateway")

_rate_limiter: RateLimiter = MemoryRateLimiter()


@app.on_event("startup")
async def _startup() -> None:
    global _rate_limiter
    settings = get_settings()
    _rate_limiter = build_rate_limiter(settings.redis_url)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/readyz")
async def readyz() -> dict[str, str]:
    settings = get_settings()
    try:
        async with httpx.AsyncClient(timeout=3) as http:
            resp = await http.get(f"{settings.orchestrator_url}/healthz")
            resp.raise_for_status()
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=503, detail=f"orchestrator unreachable: {exc}") from exc
    return {"status": "ready"}


async def _proxy_stream(payload: dict[str, object], orchestrator_url: str) -> AsyncIterator[bytes]:
    async with (
        httpx.AsyncClient(timeout=httpx.Timeout(120, connect=5)) as http,
        http.stream("POST", f"{orchestrator_url}/v1/chat", json=payload) as resp,
    ):
        resp.raise_for_status()
        async for chunk in resp.aiter_bytes():
            yield chunk


@app.post("/v1/chat")
async def chat(req: ChatRequest, request: Request) -> StreamingResponse:
    settings = get_settings()
    client_ip = request.client.host if request.client else "unknown"
    for key in (f"session:{req.session_id}", f"ip:{client_ip}"):
        if not await _rate_limiter.allow(key):
            raise HTTPException(status_code=429, detail="rate limit exceeded")

    redaction = redact(req.message)
    screened, flagged = screen_injection(redaction.redacted)
    if flagged:
        logger.warning("injection attempt flagged session=%s", req.session_id)
    if redaction.entities:
        # Raw text is persisted encrypted with the message row (Phase 4);
        # only the redacted form ever reaches the model.
        logger.info(
            "redacted %d entities session=%s kinds=%s",
            len(redaction.entities),
            req.session_id,
            sorted({k for k, _ in redaction.entities}),
        )

    forwarded = req.model_copy(update={"message": screened})
    return StreamingResponse(
        _proxy_stream(forwarded.model_dump(mode="json"), settings.orchestrator_url),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
