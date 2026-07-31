import json

import httpx
import pytest
from insurance_clients.vllm import VllmClient, VllmEndpoint, VllmError


def make_client(handler: httpx.MockTransport) -> VllmClient:
    client = VllmClient(VllmEndpoint(base_url="http://vllm.test", model="m", max_retries=1))
    client._http = httpx.AsyncClient(transport=handler, base_url="http://vllm.test")
    return client


async def test_chat_returns_content() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["model"] == "m"
        return httpx.Response(200, json={"choices": [{"message": {"content": "hello"}}]})

    client = make_client(httpx.MockTransport(handle))
    assert await client.chat([{"role": "user", "content": "hi"}]) == "hello"


async def test_chat_structured_parses_json_and_sets_response_format() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["response_format"]["type"] == "json_schema"
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"intent": "claim"}'}}]})

    client = make_client(httpx.MockTransport(handle))
    result = await client.chat_structured([{"role": "user", "content": "x"}], {"type": "object"})
    assert result == {"intent": "claim"}


async def test_embed_returns_dense_and_sparse_in_order() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": 1, "embedding": [0.2], "lexical_weights": {"claim": 0.9}},
                    {"index": 0, "embedding": [0.1]},
                ]
            },
        )

    client = make_client(httpx.MockTransport(handle))
    out = await client.embed(["a", "b"])
    assert out[0].dense == [0.1] and out[0].sparse == {}
    assert out[1].sparse == {"claim": 0.9}


async def test_retry_then_success() -> None:
    calls = {"n": 0}

    def handle(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(503, text="busy")
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    client = make_client(httpx.MockTransport(handle))
    assert await client.chat([{"role": "user", "content": "hi"}]) == "ok"
    assert calls["n"] == 2


async def test_exhausted_retries_raise() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="down")

    client = make_client(httpx.MockTransport(handle))
    with pytest.raises(VllmError):
        await client.chat([{"role": "user", "content": "hi"}])
