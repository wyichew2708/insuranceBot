"""Async client for the externally hosted OpenAI-compatible vLLM endpoints (§0).

One VllmEndpoint per role (agent / judge / embed / rerank). Retries with
exponential backoff, per-endpoint timeouts, a Langfuse span per call.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any

import httpx

from insurance_clients.observability import Tracer

RETRYABLE_STATUS = {429, 500, 502, 503, 504}


@dataclass
class VllmEndpoint:
    base_url: str
    model: str
    api_key: str = ""
    timeout_s: float = 60.0
    max_retries: int = 3


@dataclass
class Embedding:
    dense: list[float]
    sparse: dict[str, float] = field(default_factory=dict)


class VllmError(RuntimeError):
    pass


class VllmClient:
    def __init__(self, endpoint: VllmEndpoint, tracer: Tracer | None = None) -> None:
        self.endpoint = endpoint
        self.tracer = tracer or Tracer()
        headers = {}
        if endpoint.api_key:
            headers["Authorization"] = f"Bearer {endpoint.api_key}"
        self._http = httpx.AsyncClient(
            base_url=endpoint.base_url.rstrip("/"),
            headers=headers,
            timeout=endpoint.timeout_s,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _post(self, path: str, payload: dict[str, Any], span_name: str, trace_id: str) -> Any:
        last_error: Exception | None = None
        with self.tracer.span(trace_id, span_name, model=self.endpoint.model) as span:
            for attempt in range(self.endpoint.max_retries + 1):
                try:
                    resp = await self._http.post(path, json=payload)
                    if resp.status_code in RETRYABLE_STATUS:
                        raise VllmError(f"{path} -> {resp.status_code}: {resp.text[:200]}")
                    resp.raise_for_status()
                    span["attempts"] = attempt + 1
                    return resp.json()
                except (httpx.TransportError, VllmError) as exc:
                    last_error = exc
                    if attempt < self.endpoint.max_retries:
                        await asyncio.sleep(min(2**attempt, 8))
            span["error"] = str(last_error)
        raise VllmError(f"vLLM call {path} failed after retries: {last_error}") from last_error

    async def chat(
        self,
        messages: list[dict[str, str]],
        trace_id: str = "",
        temperature: float = 0.2,
        max_tokens: int = 1024,
        **extra: Any,
    ) -> str:
        payload = {
            "model": self.endpoint.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            **extra,
        }
        data = await self._post("/v1/chat/completions", payload, "vllm.chat", trace_id)
        content = data["choices"][0]["message"]["content"]
        return str(content)

    async def chat_structured(
        self,
        messages: list[dict[str, str]],
        json_schema: dict[str, Any],
        trace_id: str = "",
        temperature: float = 0.0,
        max_tokens: int = 1024,
    ) -> dict[str, Any]:
        """Guided decoding via vLLM structured outputs (response_format json_schema)."""
        payload = {
            "model": self.endpoint.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "structured_output", "schema": json_schema},
            },
        }
        data = await self._post("/v1/chat/completions", payload, "vllm.chat_structured", trace_id)
        content = data["choices"][0]["message"]["content"]
        parsed = json.loads(content)
        if not isinstance(parsed, dict):
            raise VllmError(f"structured output is not an object: {content[:200]}")
        return parsed

    async def embed(self, texts: list[str], trace_id: str = "") -> list[Embedding]:
        """BGE-M3 embeddings. Dense always; sparse lexical weights when the server returns them."""
        payload = {"model": self.endpoint.model, "input": texts}
        data = await self._post("/v1/embeddings", payload, "vllm.embed", trace_id)
        out: list[Embedding] = []
        for item in sorted(data["data"], key=lambda d: d["index"]):
            sparse = item.get("sparse_embedding") or item.get("lexical_weights") or {}
            out.append(Embedding(dense=list(item["embedding"]), sparse=dict(sparse)))
        return out

    async def rerank(self, query: str, documents: list[str], trace_id: str = "") -> list[float]:
        """BGE-reranker scores, one per document, in input order."""
        payload = {"model": self.endpoint.model, "query": query, "documents": documents}
        data = await self._post("/v1/rerank", payload, "vllm.rerank", trace_id)
        results = sorted(data["results"], key=lambda r: r["index"])
        return [float(r["relevance_score"]) for r in results]
