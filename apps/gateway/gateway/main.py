"""Gateway: the only public entrypoint.

POST /v1/chat: rate-limit -> redact PII -> injection screen -> forward to
orchestrator -> stream SSE back. Audience/brand come from the session
issuance path (JWT in Phase 4); v1 accepts them in the request body but a
widget key -> brand binding will replace client-supplied brand.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from contracts.api import ChatRequest
from contracts.settings import get_settings
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from gateway.rate_limit import MemoryRateLimiter, RateLimiter, build_rate_limiter
from gateway.redaction import redact, screen_injection
from gateway.sessions import issue_internal_session, issue_widget_session, verify_token
from gateway.storage import store_feedback, store_message

logger = logging.getLogger("gateway")

_rate_limiter: RateLimiter = MemoryRateLimiter()


@asynccontextmanager
async def _lifespan(_: FastAPI) -> AsyncIterator[None]:
    global _rate_limiter
    _rate_limiter = build_rate_limiter(get_settings().redis_url)
    yield


app = FastAPI(title="gateway", lifespan=_lifespan)


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
    try:
        async with (
            httpx.AsyncClient(timeout=httpx.Timeout(120, connect=5)) as http,
            http.stream("POST", f"{orchestrator_url}/v1/chat", json=payload) as resp,
        ):
            resp.raise_for_status()
            async for chunk in resp.aiter_bytes():
                yield chunk
    except httpx.HTTPError as exc:
        # Orchestrator unreachable mid-stream: close the turn gracefully so
        # the widget shows a retry message instead of a broken connection.
        logger.error("orchestrator stream failed: %s", exc)
        yield (
            b'data: {"type": "token", "text": '
            b'"Sorry \xe2\x80\x94 I could not process that just now. Please try again."}\n\n'
        )
        yield b'data: {"type": "done", "route": "error"}\n\n'


class SessionRequest(BaseModel):
    widget_key: str


@app.post("/v1/session")
async def create_session(req: SessionRequest, request: Request) -> dict[str, str]:
    """Issue a session JWT. Brand is bound server-side to the widget key —
    the client never chooses it (§9.1 anti-tamper). The internal portal path
    uses the SSO header stub instead of a widget key."""
    settings = get_settings()
    client_ip = request.client.host if request.client else "unknown"
    if not await _rate_limiter.allow(f"session-mint:{client_ip}"):
        raise HTTPException(status_code=429, detail="rate limit exceeded")
    if not settings.session_secret:
        raise HTTPException(status_code=503, detail="sessions not configured (SESSION_SECRET unset)")
    staff_user = request.headers.get("X-Staff-User")
    if staff_user:
        brand = request.headers.get("X-Brand", settings.brand_list[0])
        if brand not in settings.brand_list:
            raise HTTPException(status_code=400, detail="unknown brand")
        session_id, token = issue_internal_session(
            staff_user, brand, settings.session_secret, settings.session_ttl_minutes
        )
        return {"session_id": session_id, "token": token}
    issued = issue_widget_session(
        req.widget_key, settings.widget_key_map, settings.session_secret, settings.session_ttl_minutes
    )
    if issued is None:
        raise HTTPException(status_code=401, detail="unknown widget key")
    session_id, token = issued
    return {"session_id": session_id, "token": token}


def _authorize(req: ChatRequest, request: Request) -> ChatRequest:
    """Server-side claims win over anything the client sent in the body.
    Without SESSION_SECRET (dev mode) the body is trusted, except that
    `audience=internal` always requires the SSO header stub."""
    settings = get_settings()
    if settings.session_secret:
        auth = request.headers.get("Authorization", "")
        token = auth.removeprefix("Bearer ").strip()
        claims = verify_token(token, settings.session_secret) if token else None
        if claims is None:
            raise HTTPException(status_code=401, detail="missing or invalid session token")
        # Re-validate (not model_copy) so brand/audience coerce to their enums.
        return ChatRequest.model_validate(
            {
                "session_id": str(claims["sid"]),
                "brand": claims["brand"],
                "audience": claims["audience"],
                "message": req.message,
            }
        )
    if req.audience.value == "internal" and not request.headers.get("X-Staff-User"):
        raise HTTPException(status_code=403, detail="internal audience requires staff auth")
    return req


@app.post("/v1/chat")
async def chat(req: ChatRequest, request: Request) -> StreamingResponse:
    settings = get_settings()
    req = _authorize(req, request)
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

    await store_message(
        settings.database_url,
        req.session_id,
        channel="widget",
        brand=req.brand.value,
        audience=req.audience.value,
        role="user",
        content=req.message,
        redacted_content=screened,
        enc_key=settings.message_enc_key,
    )

    forwarded = req.model_copy(update={"message": screened})
    return StreamingResponse(
        _proxy_stream(forwarded.model_dump(mode="json"), settings.orchestrator_url),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/v1/actions/{brand}/{action_id}")
async def resolve_action(brand: str, action_id: str) -> dict[str, object]:
    """Renderer substitution endpoint: the widget resolves action_ids to the
    exact registered value (verbatim rule enforced mechanically, §8.2)."""
    settings = get_settings()
    if brand not in settings.brand_list:
        raise HTTPException(status_code=404, detail="unknown brand")
    try:
        async with httpx.AsyncClient(timeout=5) as http:
            resp = await http.get(f"{settings.retrieval_url}/actions/{brand}/{action_id}")
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=503, detail=f"actions registry unavailable: {exc}") from exc
    if resp.status_code == 404:
        raise HTTPException(status_code=404, detail="unknown action")
    resp.raise_for_status()
    data: dict[str, object] = resp.json()
    return data


class FeedbackRequest(BaseModel):
    session_id: str
    rating: str | int  # "up" | "down" | -1..1
    message_index: int | None = None
    comment: str | None = None


@app.post("/v1/feedback", status_code=202)
async def feedback(req: FeedbackRequest) -> dict[str, str]:
    settings = get_settings()
    rating = req.rating
    if isinstance(rating, str):
        rating = {"up": 1, "down": -1}.get(rating.lower(), 0)
    comment = redact(req.comment).redacted if req.comment else None
    stored = await store_feedback(settings.database_url, req.session_id, int(rating), comment)
    return {"status": "stored" if stored else "accepted"}
