import httpx
import pytest
from api.main import app


@pytest.fixture
async def client():  # type: ignore[no-untyped-def]
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_health_and_ready(client: httpx.AsyncClient) -> None:
    assert (await client.get("/healthz")).json()["status"] == "ok"
    ready = (await client.get("/readyz")).json()
    assert ready["pages"] > 0 and ready["table_rows"] > 0


async def test_console_is_served(client: httpx.AsyncClient) -> None:
    r = await client.get("/")
    assert r.status_code == 200
    assert "Debug Console" in r.text


async def test_bundle_reports_clean_lint(client: httpx.AsyncClient) -> None:
    info = (await client.get("/v1/bundle")).json()
    assert info["lint"]["ok"], info["lint"]
    assert info["by_type"]["product"] >= 5


async def test_answer_then_fetch_its_trace(client: httpx.AsyncClient) -> None:
    r = await client.post(
        "/v1/answer",
        json={
            "question": "What is the baggage limit and per item sub-limit?",
            "session": {
                "session_id": "t",
                "channel": "channel/direct",
                "auth_level": "L2",
                "policy": {
                    "policy_id": "TRV-100001",
                    "product_id": "product/general/travel",
                    "version": "2026.1",
                    "tier": "tier-2",
                },
            },
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["delivered"] and body["answer"]["figures"]

    trace = (await client.get(f"/v1/traces/{body['trace_id']}")).json()
    assert trace["figures_resolved"]
    assert trace["rejected"], "the console needs rejected candidates"
    names = [g["gate"] for g in trace["gates"]]
    # The seven verification gates, plus the output screen reported alongside
    # them so one list decides whether an answer ships.
    assert len(names) == 8
    assert "guardrail-output" in names


async def test_page_inspector(client: httpx.AsyncClient) -> None:
    page = (await client.get("/v1/bundle/page/product/general/travel")).json()
    assert page["frontmatter"]["id"] == "product/general/travel"
    assert "product/general/travel/exclusions" in page["neighbours"]
    assert (await client.get("/v1/bundle/page/nope")).status_code == 404


async def test_question_length_is_bounded(client: httpx.AsyncClient) -> None:
    r = await client.post(
        "/v1/answer",
        json={
            "question": "x" * 5000,
            "session": {"session_id": "t", "channel": "unknown", "auth_level": "L0"},
        },
    )
    assert r.status_code == 422


async def test_evals_run_from_the_console(client: httpx.AsyncClient) -> None:
    report = (await client.post("/v1/evals/run", json={"suite": "merge-consistency"})).json()
    assert report["total"] == 3
    assert report["pass_rate"] == 1.0
