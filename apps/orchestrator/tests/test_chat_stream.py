"""Phase 0 DoD: chat flows end-to-end and the emergency route short-circuits."""

import httpx
from contracts.api import ChatEvent, ChatEventType
from orchestrator.main import app, parse_sse_line


async def collect_events(message: str) -> list[ChatEvent]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://orch.test") as client:
        resp = await client.post(
            "/v1/chat",
            json={"session_id": "s1", "brand": "tiq", "audience": "public", "message": message},
        )
        resp.raise_for_status()
        events = []
        for line in resp.text.splitlines():
            event = parse_sse_line(line)
            if event:
                events.append(event)
        return events


async def test_health_endpoints() -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://orch.test") as client:
        assert (await client.get("/healthz")).json() == {"status": "ok"}
        assert (await client.get("/readyz")).json() == {"status": "ready"}


async def test_stub_chat_streams_token_and_done() -> None:
    events = await collect_events("Does travel insurance cover golf equipment?")
    types = [e.type for e in events]
    assert types[0] == ChatEventType.token
    assert types[-1] == ChatEventType.done
    assert events[-1].route == "coverage_qa"
    assert events[-1].trace_id


async def test_emergency_route_returns_hotline_action_before_anything_else() -> None:
    events = await collect_events("I'm in hospital overseas, this is an emergency!")
    assert events[-1].route == "emergency"
    action_events = [e for e in events if e.type == ChatEventType.action]
    assert action_events and action_events[0].action_id == "emergency-services-hotline"
    # verbatim rule: no raw phone number in the stream — renderer substitutes from actions table
    assert not any(ch.isdigit() for e in events if e.text for ch in e.text)
