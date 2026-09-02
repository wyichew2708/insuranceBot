"""Third-party integrations — what this service talks to, and whether it works.

Two directions, and they fail differently:

* **Outbound** — generation, tracing, the policy-admin system of record, the
  crawl egress. Each is optional by design; with none of them configured the
  serve loop still runs deterministically, which is what keeps the console and
  the eval suites usable offline. What matters operationally is knowing
  *which* are live, because a silently-absent tracer or a blocked crawl host
  looks exactly like everything being fine.
* **Inbound** — the answer API a partner surface calls. Documented here rather
  than only in OpenAPI, because the thing an integrator needs first is the
  session contract: channel, auth level, and which fields are entitlement-bound.

Every probe is short, times out, and reports the failure verbatim. An
integration page that rounds "403 from the proxy" up to "unavailable" is how a
blocked egress policy gets mistaken for a bug in the crawler.
"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from api.settings import Settings

PROBE_TIMEOUT_S = 6.0


@dataclass
class Integration:
    name: str
    label: str
    direction: str  # outbound | inbound
    purpose: str
    configured: bool
    required: bool
    detail: str = ""
    endpoint: str = ""
    docs: str = ""
    fallback: str = ""
    probe: str = ""  # the endpoint that tests it, if any
    fields: list[dict[str, str]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "label": self.label,
            "direction": self.direction,
            "purpose": self.purpose,
            "configured": self.configured,
            "required": self.required,
            "detail": self.detail,
            "endpoint": self.endpoint,
            "docs": self.docs,
            "fallback": self.fallback,
            "probe": self.probe,
            "fields": self.fields,
        }


def registry(settings: Settings) -> list[Integration]:
    return [
        Integration(
            name="anthropic",
            label="Anthropic — answer composition",
            direction="outbound",
            purpose=(
                "Phrases the answer under structured outputs. Numbers never come from it: "
                "figures are fetched from benefit tables, the numeric-binding gate blocks any "
                "digit that traces to no row, and a rewrite that drops a resolved figure is "
                "rejected before it reaches the gates."
            ),
            configured=bool(settings.anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY")),
            required=False,
            endpoint="https://api.anthropic.com",
            fallback="Deterministic composer — the offline path, and what every eval run uses.",
            probe="anthropic",
            fields=[
                {"key": "LLM_PROVIDER", "value": settings.llm_provider},
                {"key": "ANTHROPIC_API_KEY", "value": "set" if settings.anthropic_api_key else ""},
                {"key": "ANTHROPIC_MODEL", "value": settings.anthropic_model},
                {"key": "ANTHROPIC_EFFORT", "value": settings.anthropic_effort},
            ],
        ),
        Integration(
            name="vllm",
            label="vLLM — answer composition (self-hosted)",
            direction="outbound",
            purpose=(
                "The same job as the Anthropic provider, on a model you host. Held to the "
                "identical JSON schema via guided_json, so switching engines changes the "
                "runtime, not the answer contract."
            ),
            configured=bool(settings.vllm_base_url),
            required=False,
            endpoint=settings.vllm_base_url or "(unset)",
            fallback="Deterministic composer — the offline path, and what every eval run uses.",
            probe="vllm",
            fields=[
                {"key": "LLM_PROVIDER", "value": settings.llm_provider},
                {"key": "VLLM_BASE_URL", "value": settings.vllm_base_url or ""},
                {"key": "VLLM_MODEL", "value": settings.vllm_model or ""},
                {"key": "VLLM_API_KEY", "value": "set" if settings.vllm_api_key else ""},
            ],
        ),
        Integration(
            name="pgvector",
            label="pgvector — dense retrieval (self-hosted)",
            direction="outbound",
            purpose=(
                "Vector search over the compiled wiki sections, fused with the lexical "
                "rank. Recall only: a chunk found by similarity goes through the same "
                "frontmatter filter, composition and gates as one found by words."
            ),
            configured=bool(settings.pgvector_dsn) and settings.pgvector.lower() != "off",
            required=False,
            endpoint=_redact_dsn(settings.pgvector_dsn) if settings.pgvector_dsn else "(unset)",
            fallback="Lexical retrieval — the offline path, and what every turn ran on before v2.1.",
            probe="pgvector",
            fields=[
                {"key": "PGVECTOR", "value": settings.pgvector},
                {"key": "PGVECTOR_DSN", "value": "set" if settings.pgvector_dsn else ""},
                {"key": "EMBED_BASE_URL", "value": settings.embed_base_url or ""},
                {"key": "EMBED_MODEL", "value": settings.embed_model},
                {"key": "RERANK_BASE_URL", "value": settings.rerank_base_url or ""},
                {"key": "PGVECTOR_FAIL_CLOSED", "value": str(settings.pgvector_fail_closed).lower()},
            ],
        ),
        Integration(
            name="langfuse",
            label="Langfuse — trace export",
            direction="outbound",
            purpose="Ships the per-turn trace: candidates, gates, budgets, latencies.",
            configured=bool(settings.langfuse_host and settings.langfuse_public_key),
            required=False,
            endpoint=settings.langfuse_host or "(unset)",
            fallback="In-process trace store; the debug console reads it directly.",
            probe="langfuse",
            fields=[
                {"key": "LANGFUSE_HOST", "value": settings.langfuse_host or ""},
                {"key": "LANGFUSE_PUBLIC_KEY", "value": "set" if settings.langfuse_public_key else ""},
                {"key": "LANGFUSE_SECRET_KEY", "value": "set" if settings.langfuse_secret_key else ""},
            ],
        ),
        Integration(
            name="sor",
            label="Policy admin — system of record",
            direction="outbound",
            purpose=(
                "Customer-specific facts — plan tier, in-force version, policy status. Never in "
                "the wiki, which describes only what is currently sold, and only ever read behind "
                "an entitlement predicate."
            ),
            configured=False,
            required=True,
            endpoint="(fixture)",
            fallback="Fixture policies derived from the bundle's benefit tables.",
            probe="sor",
            fields=[{"key": "implementation", "value": "api/sor.py — swap the body, keep the shape"}],
        ),
        Integration(
            name="crawl-egress",
            label="Crawl egress — etiqa.com.sg, tiq.com.sg",
            direction="outbound",
            purpose="Reachability of the sites a scan reads. Nothing else in this service needs them.",
            configured=True,
            required=True,
            endpoint="https://www.etiqa.com.sg, https://www.tiq.com.sg",
            fallback="The synthetic fixture site, served in-process. Labelled as such everywhere.",
            probe="crawl-egress",
        ),
        Integration(
            name="answer-api",
            label="Answer API — partner surfaces",
            direction="inbound",
            purpose=(
                "One call per turn. The caller supplies the session; the response carries the "
                "answer, its claims with source ids, every figure with its table row id, and all "
                "seven gate verdicts. A blocked answer is a 200 with delivered=false, not an error."
            ),
            configured=True,
            required=True,
            endpoint="POST /v1/answer",
            docs="/docs",
            probe="answer-api",
            fields=[
                {
                    "key": "session.channel",
                    "value": "channel/direct · bancassurance · agency · broker · ifa · unknown",
                },
                {"key": "session.auth_level", "value": "L0 anonymous · L1 identified · L2 authenticated"},
                {"key": "session.policy", "value": "required for tier-specific figures (L2 only)"},
            ],
        ),
        Integration(
            name="content-api",
            label="Content API — portal and CI",
            direction="inbound",
            purpose=(
                "Read and write the corpus: pages, tags, status transitions, scans, suggestions. "
                "Every write is linted against a shadow bundle before it touches disk."
            ),
            configured=True,
            required=True,
            endpoint="/v1/cms/*",
            docs="/docs",
            fields=[
                {"key": "bundle", "value": str(settings.bundle_path)},
                {"key": "guarantee", "value": "a page that fails the linter is never written"},
            ],
        ),
    ]


async def probe(name: str, settings: Settings) -> dict[str, Any]:
    """Test one integration. Never raises: a probe that throws is a probe that
    tells you nothing."""
    started = time.perf_counter()

    async def done(ok: bool, detail: str, **extra: Any) -> dict[str, Any]:
        return {
            "name": name,
            "ok": ok,
            "detail": detail,
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
            **extra,
        }

    if name == "anthropic":
        key = settings.anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        if not key:
            return await done(
                False,
                "no API key — set ANTHROPIC_API_KEY, or run `ant auth login` and the SDK "
                "resolves a stored profile",
            )
        # /v1/models is the cheapest authenticated call; it proves the key
        # without spending a single token on a completion.
        ok, detail = await _http_ok(
            "https://api.anthropic.com/v1/models",
            headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
        )
        return await done(ok, detail, model=settings.anthropic_model)

    if name == "vllm":
        if not settings.vllm_base_url:
            return await done(False, "not configured — the deterministic composer is in use")
        return await done(*await _http_ok(f"{settings.vllm_base_url.rstrip('/')}/v1/models"))

    if name == "pgvector":
        if not settings.pgvector_dsn or settings.pgvector.lower() == "off":
            return await done(False, "not configured — lexical retrieval is in use")
        # The database's own words, verbatim. Rounding "password authentication
        # failed" up to "unavailable" is how a wrong DSN gets mistaken for a
        # network problem.
        ok, detail = await asyncio.to_thread(
            _pgvector_probe, settings.pgvector_dsn, str(settings.bundle_path)
        )
        embed = ""
        if settings.embed_base_url:
            e_ok, e_detail = await _http_ok(f"{settings.embed_base_url.rstrip('/')}/models")
            embed = f"; embeddings {'ok' if e_ok else 'FAILED: ' + e_detail}"
            ok = ok and e_ok
        return await done(ok, detail + embed)

    if name == "langfuse":
        if not (settings.langfuse_host and settings.langfuse_public_key):
            return await done(False, "not configured — traces stay in the in-process store")
        return await done(*await _http_ok(f"{settings.langfuse_host.rstrip('/')}/api/public/health"))

    if name == "sor":
        from api.sor import FIXTURE_POLICIES

        return await done(
            True,
            f"fixture implementation, {len(FIXTURE_POLICIES)} policies derived from the benefit tables",
        )

    if name == "crawl-egress":
        results = await asyncio.gather(
            _http_ok("https://www.etiqa.com.sg/robots.txt"),
            _http_ok("https://www.tiq.com.sg/robots.txt"),
        )
        reachable = [ok for ok, _ in results]
        detail = " · ".join(d for _, d in results)
        return await done(
            all(reachable),
            detail,
            hosts=[
                {"host": "www.etiqa.com.sg", "ok": results[0][0], "detail": results[0][1]},
                {"host": "www.tiq.com.sg", "ok": results[1][0], "detail": results[1][1]},
            ],
        )

    if name == "answer-api":
        return await done(True, "served by this process")

    if name == "content-api":
        root = Path(settings.bundle_path)
        writable = root.is_dir()
        return await done(writable, f"{root} {'is writable' if writable else 'does not exist'}")

    return await done(False, f"unknown integration {name!r}")


async def _http_ok(url: str, headers: dict[str, str] | None = None) -> tuple[bool, str]:
    import httpx

    try:
        async with httpx.AsyncClient(timeout=PROBE_TIMEOUT_S, follow_redirects=True) as client:
            response = await client.get(url, headers=headers)
        return response.status_code < 400, f"{url} → HTTP {response.status_code}"
    except Exception as exc:
        # Verbatim. "403 to CONNECT" is a policy decision someone made, and
        # rounding it to "unreachable" sends the reader hunting for a bug.
        return False, f"{url} → {type(exc).__name__}: {exc}"


def _redact_dsn(dsn: str) -> str:
    """The DSN with its password removed, for a page anyone on the team reads."""
    if "@" in dsn and "://" in dsn:
        scheme, rest = dsn.split("://", 1)
        creds, host = rest.rsplit("@", 1)
        user = creds.split(":", 1)[0]
        return f"{scheme}://{user}:***@{host}"
    return dsn


def _pgvector_probe(dsn: str, bundle: str) -> tuple[bool, str]:
    """Connect, and read the index fingerprint for this bundle. Synchronous —
    run under `asyncio.to_thread` by the caller."""
    try:
        import psycopg
    except ImportError:
        return False, "psycopg not installed — install the api package's `pgvector` extra"
    try:
        with psycopg.connect(dsn, connect_timeout=int(PROBE_TIMEOUT_S)) as conn:
            row = conn.execute(
                "SELECT chunks, fingerprint FROM chunk_fingerprint WHERE bundle = %s", (bundle,)
            ).fetchone()
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"
    if row is None:
        return False, f"connected; no index for bundle {bundle!r} — run `make index`"
    return True, f"connected; {row[0]} chunks indexed for {bundle!r} (fingerprint {str(row[1])[:12]}...)"
