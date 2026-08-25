"""Generation providers (§H.1).

The model's job here is deliberately small. Retrieval has already chosen the
pages, the transclusion pass has already resolved every figure against a
benefit-table row, and the composer has already lifted each `[src:...]` marker
into a typed Claim. What is left is *prose* — and that is all a provider is
asked for.

This is the whole reason a model can be swapped in without weakening the
guarantees: it never fetches a fact, so it cannot invent one and have it
believed. Whatever it writes goes through the same seven gates as the
deterministic composer, and `numeric-binding` blocks any figure that no row
produced. A provider that hallucinates is caught by construction, not by
asking it to be careful.

Three providers, one contract:

  deterministic  the composer's own prose. No network, no key. The default,
                 and what CI and the eval suites run on.
  anthropic      Claude via the official SDK, under structured outputs.
  vllm           a locally hosted model over vLLM's OpenAI-compatible route,
                 under the *same* JSON schema via `guided_json`.

Both model providers are handed the identical schema, so switching between a
frontier model and a local one changes the engine, not the contract.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx
from harness import Claim, Figure

#: Guided-decoding schema. Anthropic takes it as `output_config.format`;
#: vLLM takes the same object as `guided_json`. Keeping one definition is what
#: makes the two engines comparable rather than merely both "supported".
REWRITE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "answer": {
            "type": "string",
            "description": (
                "The answer to the customer, in plain prose. Every figure from "
                "the supplied facts must appear verbatim; no other numbers may "
                "appear at all."
            ),
        },
        "unresolved": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "Anything the supplied facts did not establish. Say so here "
                "rather than writing it into the answer."
            ),
        },
    },
    "required": ["answer", "unresolved"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """\
You write the customer-facing prose for a Singapore general-insurance assistant.

You are given facts that have already been verified against the product wiki \
and the benefit tables. Write the answer from those facts and nothing else.

Rules, in order of importance:
1. Every figure in the facts must appear in your answer exactly as written — \
same digits, same currency symbol, same units. Do not round, convert, \
recalculate, or restate a figure in words.
2. Introduce no number that is not in the facts. Not a limit, not a duration, \
not a percentage, not an excess. If the customer asked for one that is absent, \
put it in `unresolved` instead of estimating it.
3. Make no claim the facts do not support. Do not add context you happen to \
know about insurance generally.
4. Keep the customer's own words for the product where the facts allow it.
5. Answer at the length the question needs — usually two or three sentences. \
No preamble, no restating the question, no closing offer of further help.

A downstream check verifies every figure against its source row and blocks the \
answer if one does not match, so an invented number is not a risk you are \
taking on the customer's behalf — it is an answer that will not be delivered.
"""


@dataclass
class Draft:
    """What the composer established, handed to a provider to phrase."""

    question: str
    prose: str
    claims: list[Claim] = field(default_factory=list)
    figures: list[Figure] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)

    def accepts(self, answer: str) -> bool:
        """Whether `answer` is a faithful rewrite of this draft.

        The gates catch a figure the model *invented*. They cannot catch one
        it silently *dropped*: an answer containing no numbers has no unbound
        numbers, so numeric-binding passes vacuously and a non-answer is
        delivered. So a rewrite must still carry every figure the composer
        had already put in the prose — verbatim, since these are the exact
        strings a benefit-table row produced.
        """
        required = [f.text for f in self.figures if f.text and f.text in self.prose]
        return all(text in answer for text in required)

    def facts_block(self) -> str:
        lines = [f"QUESTION: {self.question}", "", "FACTS ESTABLISHED:"]
        for claim in self.claims:
            locator = f" [{claim.locator}]" if claim.locator else ""
            lines.append(f"- {claim.text}  (source: {claim.source_id}{locator})")
        if self.figures:
            lines += ["", "FIGURES — reproduce each of these verbatim if you use it:"]
            for figure in self.figures:
                binding = figure.table_row_id or figure.sor_field or figure.page_ref or "unbound"
                lines.append(f"- {figure.label}: {figure.text}  (bound to {binding})")
        if self.unresolved:
            lines += ["", "NOT ESTABLISHED — do not guess these:"]
            lines += [f"- {item}" for item in self.unresolved]
        lines += ["", "DETERMINISTIC DRAFT (rephrase; do not add to it):", self.prose]
        return "\n".join(lines)


@dataclass
class Rewrite:
    answer: str
    unresolved: list[str] = field(default_factory=list)
    provider: str = "deterministic"
    model: str = ""
    tokens: int = 0


class LLMProvider(Protocol):
    name: str

    def rewrite(self, draft: Draft) -> Rewrite | None:
        """Phrase `draft`, or return None to keep the deterministic prose.

        Returning None rather than raising is the point: a provider that is
        down, throttled, or misconfigured degrades to the composer's own
        wording instead of failing the customer's question.
        """

    def classify(
        self, system: str, user: str, schema: dict[str, Any], *, model: str = "", max_tokens: int = 1024
    ) -> dict[str, Any] | None:
        """A judging call rather than a writing one: the model reads text and
        returns a verdict under `schema`, and none of what it returns reaches
        the customer.

        Separate from `rewrite` because the failure modes invert. A rewrite
        that fails should fall back to prose the composer already trusts. A
        classification that fails has no safe substitute — the caller has to
        decide what an unscreened turn is worth, which is why this returns
        None instead of guessing a verdict.
        """


class DeterministicProvider:
    """No model. The composer's prose is the answer."""

    name = "deterministic"

    def rewrite(self, draft: Draft) -> Rewrite | None:
        return None

    def classify(
        self, system: str, user: str, schema: dict[str, Any], *, model: str = "", max_tokens: int = 1024
    ) -> dict[str, Any] | None:
        """No model, so no judgement. The caller's deterministic rules stand
        on their own rather than being handed a fabricated verdict."""
        return None


def _json_object(payload: str) -> dict[str, Any] | None:
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _parsed(payload: str) -> tuple[str, list[str]] | None:
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return None
    answer = data.get("answer")
    if not isinstance(answer, str) or not answer.strip():
        return None
    unresolved = [str(u) for u in data.get("unresolved", []) if str(u).strip()]
    return answer.strip(), unresolved


#: Models observed to reject `output_config.effort`. Discovered at runtime
#: rather than hardcoded: a list of model ids goes stale the week a new one
#: ships, and the failure mode is silent — `classify` returns None on any
#: exception, so an unsupported parameter reads as "the model layer is off"
#: while every setting says it is on. Haiku 4.5 is the current example.
_NO_EFFORT: set[str] = set()


def _rejects_effort(exc: Exception) -> bool:
    return "effort" in str(exc).lower() and "does not support" in str(exc).lower()


class AnthropicProvider:
    """Claude via the official SDK, under structured outputs."""

    name = "anthropic"

    def __init__(
        self,
        model: str = "claude-sonnet-5",
        effort: str = "low",
        max_tokens: int = 8192,
        api_key: str = "",
        timeout_s: float = 30.0,
    ) -> None:
        self.model = model
        self.effort = effort
        # Omitting `thinking` runs adaptive thinking on Sonnet 5 and Opus 5,
        # and it is charged against max_tokens along with the reply — so this
        # is sized for both, even though the reply is a few sentences.
        self.max_tokens = max_tokens
        self.api_key = api_key
        self.timeout_s = timeout_s
        self._client: Any = None

    def _load(self) -> Any:
        if self._client is None:
            import anthropic  # imported lazily: the package is an extra

            key = self.api_key or os.environ.get("ANTHROPIC_API_KEY", "")
            # An empty key still resolves an `ant auth login` profile, so let
            # the SDK do its own credential resolution rather than pre-empting it.
            self._client = (
                anthropic.Anthropic(api_key=key, timeout=self.timeout_s)
                if key
                else anthropic.Anthropic(timeout=self.timeout_s)
            )
        return self._client

    def _create(self, model: str, system: str, user: str, schema: dict[str, Any], max_tokens: int) -> Any:
        """One structured call, retried once without `effort` if the model does
        not take it.

        Only some models accept an effort hint, and the ones that do are worth
        giving it — a rewrite or a classification should not spend a thinking
        budget. Rather than keep a list, ask once and remember the answer.
        """
        client = self._load()
        fmt: dict[str, Any] = {"format": {"type": "json_schema", "schema": schema}}
        for attempt in range(2):
            config = dict(fmt)
            if model not in _NO_EFFORT and attempt == 0:
                config["effort"] = self.effort
            try:
                return client.messages.create(
                    model=model,
                    max_tokens=max_tokens,
                    system=system,
                    output_config=config,
                    messages=[{"role": "user", "content": user}],
                )
            except Exception as exc:
                if attempt == 0 and _rejects_effort(exc):
                    _NO_EFFORT.add(model)
                    continue
                raise

    def rewrite(self, draft: Draft) -> Rewrite | None:
        try:
            response = self._create(
                self.model, SYSTEM_PROMPT, draft.facts_block(), REWRITE_SCHEMA, self.max_tokens
            )
        except Exception:
            # Down, throttled, or misconfigured: fall back to the draft.
            return None
        if getattr(response, "stop_reason", None) == "refusal":
            return None
        text = next((b.text for b in response.content if b.type == "text"), "")
        parsed = _parsed(text)
        if parsed is None:
            return None
        answer, unresolved = parsed
        usage = getattr(response, "usage", None)
        tokens = (getattr(usage, "input_tokens", 0) + getattr(usage, "output_tokens", 0)) if usage else 0
        return Rewrite(answer, unresolved, self.name, self.model, tokens)

    def classify(
        self, system: str, user: str, schema: dict[str, Any], *, model: str = "", max_tokens: int = 1024
    ) -> dict[str, Any] | None:
        try:
            # Judging is a shallow task on the request path of every turn, so
            # it is not given a thinking budget where the model accepts one.
            response = self._create(model or self.model, system, user, schema, max_tokens)
        except Exception:
            return None
        if getattr(response, "stop_reason", None) == "refusal":
            return None
        text = next((b.text for b in response.content if b.type == "text"), "")
        return _json_object(text)


class VllmProvider:
    """A locally hosted model over vLLM's OpenAI-compatible route.

    vLLM's `guided_json` takes the same schema Anthropic gets, so the local
    model is held to the identical output contract.
    """

    name = "vllm"

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str = "",
        max_tokens: int = 1024,
        temperature: float = 0.0,
        timeout_s: float = 30.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.timeout_s = timeout_s
        #: Overridable in tests; otherwise a pooled client is built on first use.
        self._transport: httpx.BaseTransport | None = None
        self._http: httpx.Client | None = None

    def _client(self) -> httpx.Client:
        if self._http is None:
            headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
            self._http = httpx.Client(
                base_url=self.base_url,
                headers=headers,
                timeout=self.timeout_s,
                transport=self._transport,
            )
        return self._http

    def rewrite(self, draft: Draft) -> Rewrite | None:
        payload: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": draft.facts_block()},
            ],
            # vLLM's guided decoding. `response_format` is the portable
            # spelling; `guided_json` is what older vLLM builds read.
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "rewrite", "schema": REWRITE_SCHEMA, "strict": True},
            },
            "guided_json": REWRITE_SCHEMA,
        }
        try:
            response = self._client().post("/v1/chat/completions", json=payload)
            response.raise_for_status()
            body = response.json()
        except Exception:
            return None
        try:
            text = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            return None
        parsed = _parsed(text)
        if parsed is None:
            return None
        answer, unresolved = parsed
        tokens = int((body.get("usage") or {}).get("total_tokens", 0))
        return Rewrite(answer, unresolved, self.name, self.model, tokens)

    def classify(
        self, system: str, user: str, schema: dict[str, Any], *, model: str = "", max_tokens: int = 1024
    ) -> dict[str, Any] | None:
        payload: dict[str, Any] = {
            "model": model or self.model,
            "max_tokens": max_tokens,
            "temperature": 0.0,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "verdict", "schema": schema, "strict": True},
            },
            "guided_json": schema,
        }
        try:
            response = self._client().post("/v1/chat/completions", json=payload)
            response.raise_for_status()
            body = response.json()
            return _json_object(body["choices"][0]["message"]["content"])
        except Exception:
            return None


def provider_for(settings: Any) -> LLMProvider:
    """The provider named by settings, or the one its credentials imply.

    `auto` is the default so that an unconfigured checkout runs deterministically
    and a configured one does not need a second setting changed to take effect.
    """
    choice = (getattr(settings, "llm_provider", "") or "auto").lower()

    def anthropic_provider() -> LLMProvider:
        return AnthropicProvider(
            model=settings.anthropic_model,
            effort=settings.anthropic_effort,
            max_tokens=settings.anthropic_max_tokens,
            api_key=settings.anthropic_api_key,
            timeout_s=settings.llm_timeout_s,
        )

    def vllm_provider() -> LLMProvider:
        return VllmProvider(
            base_url=settings.vllm_base_url,
            model=settings.vllm_model,
            api_key=settings.vllm_api_key,
            max_tokens=settings.vllm_max_tokens,
            timeout_s=settings.llm_timeout_s,
        )

    if choice == "anthropic":
        return anthropic_provider()
    if choice == "vllm":
        return vllm_provider()
    if choice == "deterministic":
        return DeterministicProvider()
    # auto — local endpoint wins if one is configured, since running a model
    # you host is the cheaper default when both are available.
    if settings.vllm_base_url and settings.vllm_model:
        return vllm_provider()
    if settings.anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY"):
        return anthropic_provider()
    return DeterministicProvider()
