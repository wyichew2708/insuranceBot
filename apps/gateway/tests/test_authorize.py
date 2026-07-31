"""Gateway authorization: internal audience is never client-claimable (§9.2)."""

import httpx
import pytest
from gateway.main import app


@pytest.fixture
def client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gw.test")


async def test_internal_audience_without_staff_header_is_403(client: httpx.AsyncClient) -> None:
    async with client:
        resp = await client.post(
            "/v1/chat",
            json={"session_id": "s1", "brand": "etiqa", "audience": "internal", "message": "hi"},
        )
        assert resp.status_code == 403


async def test_session_endpoint_unconfigured_returns_503(client: httpx.AsyncClient) -> None:
    async with client:
        resp = await client.post("/v1/session", json={"widget_key": "any"})
        assert resp.status_code == 503


async def test_token_claims_override_spoofed_body(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SESSION_SECRET", "test-secret")
    monkeypatch.setenv("WIDGET_KEYS", "wk-tiq:tiq")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://gw.test") as c:
        session = await c.post("/v1/session", json={"widget_key": "wk-tiq"})
        assert session.status_code == 200
        token = session.json()["token"]
        # Body claims etiqa/internal; token says tiq/public — token must win
        # and the request must not 500 on enum coercion.
        resp = await c.post(
            "/v1/chat",
            headers={"Authorization": f"Bearer {token}"},
            json={"session_id": "spoof", "brand": "etiqa", "audience": "internal", "message": "hi"},
        )
        # Orchestrator isn't running in unit tests; the gateway must still
        # accept the request (authorize passed, no coercion crash) and close
        # the stream gracefully.
        assert resp.status_code == 200
        assert '"type": "done"' in resp.text


async def test_missing_token_rejected_when_sessions_enforced(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SESSION_SECRET", "test-secret")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://gw.test") as c:
        resp = await c.post(
            "/v1/chat",
            json={"session_id": "x", "brand": "tiq", "audience": "public", "message": "hi"},
        )
        assert resp.status_code == 401
