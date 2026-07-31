import httpx
from gateway.main import app


async def test_feedback_accepted_without_db() -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://gw.test") as client:
        resp = await client.post(
            "/v1/feedback", json={"session_id": "s1", "rating": "up", "message_index": 2}
        )
        assert resp.status_code == 202
        assert resp.json() == {"status": "accepted"}


async def test_feedback_comment_is_redacted_before_storage() -> None:
    # No DB configured -> nothing stored, but the endpoint must still accept
    # and the redaction path must not raise on PII in comments.
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://gw.test") as client:
        resp = await client.post(
            "/v1/feedback",
            json={"session_id": "s1", "rating": "down", "comment": "my NRIC S1234567D was shown!"},
        )
        assert resp.status_code == 202
