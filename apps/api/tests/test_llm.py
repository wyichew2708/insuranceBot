"""The generation layer (§H.1).

Two things are being pinned here. First, that a provider can be swapped
without the answer contract changing. Second — and this is the one that
matters — that a model which drifts is *caught by the gates* rather than
believed. The whole reason a model can be let near this pipeline is that it
is never the thing that establishes a fact.
"""

import json
from collections.abc import Callable

import httpx
import pytest
from api.guardrails import INPUT_PROMPT, INPUT_SCHEMA
from api.llm import (
    REWRITE_SCHEMA,
    AnthropicProvider,
    DeterministicProvider,
    Draft,
    VllmProvider,
    provider_for,
)
from api.pipeline import answer_question
from api.settings import Settings
from harness import Claim, Figure

from conftest import make_session
from okf import Bundle

DRAFT = Draft(
    question="What is the overseas medical expenses limit?",
    prose="The overseas medical expenses limit for the plan tier held is S$1,000,000.",
    claims=[Claim(text="Overseas medical expenses are covered.", source_id="product/general/travel")],
    figures=[
        Figure(
            label="medical_expenses.limit",
            text="S$1,000,000",
            table_row_id="travel:2026.1:tier-2:medical_expenses",
        )
    ],
)


# --- the draft handed to a provider ----------------------------------------


def test_facts_block_carries_every_figure_verbatim() -> None:
    block = DRAFT.facts_block()
    assert "S$1,000,000" in block
    assert "travel:2026.1:tier-2:medical_expenses" in block
    # The deterministic prose goes too — the model rephrases, it does not
    # start from the question alone.
    assert DRAFT.prose in block


def test_deterministic_provider_keeps_the_composed_prose() -> None:
    assert DeterministicProvider().rewrite(DRAFT) is None


# --- vLLM ------------------------------------------------------------------


Handler = Callable[[httpx.Request], httpx.Response]


def _vllm(handler: Handler) -> VllmProvider:
    provider = VllmProvider(base_url="http://vllm.test", model="local-model")
    provider._transport = httpx.MockTransport(handler)
    return provider


def test_vllm_sends_the_shared_schema_as_guided_json() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": json.dumps({"answer": "Up to S$1,000,000.", "unresolved": []})}}
                ],
                "usage": {"total_tokens": 42},
            },
        )

    result = _vllm(handler).rewrite(DRAFT)
    assert result is not None
    assert result.answer == "Up to S$1,000,000."
    assert result.provider == "vllm" and result.tokens == 42
    # The local model is held to the same contract the hosted one is.
    assert seen["guided_json"] == REWRITE_SCHEMA
    assert seen["response_format"]["json_schema"]["schema"] == REWRITE_SCHEMA  # type: ignore[index]


@pytest.mark.parametrize(
    "handler",
    [
        lambda request: httpx.Response(500, text="boom"),
        lambda request: httpx.Response(200, json={"choices": []}),
        lambda request: httpx.Response(200, json={"choices": [{"message": {"content": "not json"}}]}),
        lambda request: httpx.Response(
            200, json={"choices": [{"message": {"content": json.dumps({"answer": "  "})}}]}
        ),
    ],
    ids=["http-error", "no-choices", "unparseable", "empty-answer"],
)
def test_vllm_degrades_to_the_draft_rather_than_failing(handler: Handler) -> None:
    """A provider that is down must cost the customer nothing."""
    assert _vllm(handler).rewrite(DRAFT) is None


# --- Anthropic -------------------------------------------------------------


class _Block:
    type = "text"

    def __init__(self, text: str) -> None:
        self.text = text


class _Response:
    def __init__(self, payload: str, stop_reason: str = "end_turn") -> None:
        self.content = [_Block(payload)]
        self.stop_reason = stop_reason
        self.usage = type("U", (), {"input_tokens": 10, "output_tokens": 5})()


class _FakeAnthropic:
    def __init__(self, response: object) -> None:
        self.kwargs: dict[str, object] = {}
        self.messages = type("M", (), {"create": lambda _self, **kw: (self.kwargs.update(kw), response)[1]})()


def _anthropic(response: object) -> tuple[AnthropicProvider, _FakeAnthropic]:
    provider = AnthropicProvider()  # default model
    fake = _FakeAnthropic(response)
    provider._client = fake
    return provider, fake


def test_anthropic_requests_structured_output_and_returns_the_answer() -> None:
    payload = json.dumps({"answer": "Up to S$1,000,000 for the tier held.", "unresolved": ["excess"]})
    provider, fake = _anthropic(_Response(payload))
    result = provider.rewrite(DRAFT)
    assert result is not None
    assert result.answer == "Up to S$1,000,000 for the tier held."
    assert result.unresolved == ["excess"]
    assert result.tokens == 15
    assert fake.kwargs["model"] == Settings().anthropic_model
    output_config = fake.kwargs["output_config"]
    assert output_config["format"]["schema"] == REWRITE_SCHEMA  # type: ignore[index]
    assert output_config["effort"] == "low"  # type: ignore[index]


def test_anthropic_sends_nothing_sonnet_5_rejects() -> None:
    """Sonnet 5 rejects sampling params, `budget_tokens`, and a trailing
    assistant prefill. None of them should ever be built into the request."""
    payload = json.dumps({"answer": "Up to S$1,000,000.", "unresolved": []})
    _provider, fake = _anthropic(_Response(payload))
    _provider.rewrite(DRAFT)
    for rejected in ("temperature", "top_p", "top_k", "thinking"):
        assert rejected not in fake.kwargs
    assert "budget_tokens" not in json.dumps(fake.kwargs["output_config"])
    messages: list[dict[str, str]] = fake.kwargs["messages"]  # type: ignore[assignment]
    roles = [m["role"] for m in messages]
    assert roles == ["user"], "a trailing assistant turn is a prefill and 400s"


def test_anthropic_refusal_keeps_the_deterministic_prose() -> None:
    provider, _ = _anthropic(_Response(json.dumps({"answer": "x", "unresolved": []}), "refusal"))
    assert provider.rewrite(DRAFT) is None


def test_anthropic_transport_failure_keeps_the_deterministic_prose() -> None:
    provider = AnthropicProvider()

    class _Boom:
        messages = type("M", (), {"create": lambda _s, **kw: (_ for _ in ()).throw(RuntimeError("down"))})()

    provider._client = _Boom()
    assert provider.rewrite(DRAFT) is None


# --- selection -------------------------------------------------------------


def test_auto_falls_back_to_deterministic_when_nothing_is_configured() -> None:
    settings = Settings(anthropic_api_key="", vllm_base_url="", vllm_model="")
    assert provider_for(settings).name == "deterministic"


def test_auto_prefers_a_configured_local_endpoint() -> None:
    # `auto` stated rather than assumed: this test is about how auto resolves,
    # and the suite pins the ambient default to deterministic so a configured
    # machine cannot turn the tests into billed API calls.
    settings = Settings(
        llm_provider="auto", vllm_base_url="http://vllm.test", vllm_model="m", anthropic_api_key="sk-test"
    )
    assert provider_for(settings).name == "vllm"


def test_explicit_choice_overrides_what_is_configured() -> None:
    settings = Settings(llm_provider="anthropic", vllm_base_url="http://vllm.test", vllm_model="m")
    assert provider_for(settings).name == "anthropic"


# --- rewrite fidelity ------------------------------------------------------


def test_a_rewrite_that_keeps_the_figure_is_accepted() -> None:
    assert DRAFT.accepts("Covered up to S$1,000,000 for your tier.")


def test_a_rewrite_that_drops_the_figure_is_rejected() -> None:
    """The gates catch an invented figure; only this catches a dropped one.

    An answer with no numbers has no *unbound* numbers, so numeric-binding
    passes vacuously and a non-answer would be delivered.
    """
    assert not DRAFT.accepts("Your overseas medical expenses are covered.")


def test_a_figure_the_composer_never_used_is_not_required() -> None:
    """Fidelity is measured against the prose, not the figure list — the
    composer may resolve a figure it had no sentence for."""
    draft = Draft(
        question="q",
        prose="Overseas medical expenses are covered.",
        figures=[Figure(label="l", text="S$1,000,000", table_row_id="r")],
    )
    assert draft.accepts("Overseas medical costs are covered.")


class _DroppingProvider:
    name = "dropping"

    def rewrite(self, draft: Draft):  # type: ignore[no-untyped-def]
        from api.llm import Rewrite

        return Rewrite("Your medical expenses are covered.", [], "dropping", "test", 0)


def test_pipeline_keeps_deterministic_prose_when_a_figure_is_dropped(
    bundle: Bundle, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("api.pipeline.provider_for", lambda _settings: _DroppingProvider())
    envelope, trace = answer_question(
        bundle, "What is the overseas medical expenses limit?", make_session(), settings
    )
    assert envelope.delivered
    generate = next(s for s in trace.stages if s.name == "generate")
    assert generate.detail["applied"] is False
    assert generate.detail["fell_back"] == "dropped a resolved figure"
    # The figure the composer resolved is still in front of the customer.
    assert any(f.text in envelope.answer.answer for f in envelope.answer.figures)


# --- the point: the gates judge the model ----------------------------------


class _LyingProvider:
    """A model that keeps the real figure and *adds* one of its own.

    This is the dangerous shape: dropping a figure is caught by the fidelity
    check before the gates ever run, so an embellishment that passes fidelity
    is what actually tests numeric-binding.
    """

    name = "lying"

    def rewrite(self, draft: Draft):  # type: ignore[no-untyped-def]
        from api.llm import Rewrite

        kept = " ".join(f.text for f in draft.figures if f.text in draft.prose)
        return Rewrite(
            f"Reimbursed up to {kept}, with a further S$99,000,000 of emergency cover.",
            [],
            "lying",
            "test",
            0,
        )


def test_a_model_that_invents_a_figure_is_blocked(
    bundle: Bundle, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """This is the reason a model is allowed near the pipeline at all."""
    monkeypatch.setattr("api.pipeline.provider_for", lambda _settings: _LyingProvider())
    envelope, trace = answer_question(
        bundle, "What is the overseas medical expenses limit?", make_session(), settings
    )
    assert not envelope.delivered
    numeric = next(g for g in envelope.gates if g.gate == "numeric-binding")
    assert numeric.verdict.value == "fail"
    assert "99,000,000" in numeric.detail
    assert trace.composer == "lying:test"


# --- classify: the judging call, not the writing one -----------------------


def test_vllm_classify_holds_the_guardrail_schema_too() -> None:
    """A guardrail is only worth its verdict if the verdict is well formed, so
    the screening call is bound by guided decoding exactly as the rewrite is."""
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {"findings": [{"category": "advice", "risk": "flag", "detail": "d"}]}
                            )
                        }
                    }
                ]
            },
        )

    verdict = _vllm(handler).classify(INPUT_PROMPT, "turn", INPUT_SCHEMA, max_tokens=256)
    assert verdict == {"findings": [{"category": "advice", "risk": "flag", "detail": "d"}]}
    assert seen["guided_json"] == INPUT_SCHEMA
    assert seen["max_tokens"] == 256
    # Screening is a judgement, not a sample.
    assert seen["temperature"] == 0.0


def test_vllm_classify_returns_none_when_the_endpoint_fails() -> None:
    """None rather than an empty verdict: the caller decides what an
    unscreened turn is worth, and cannot do that if a failure looks clean."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    assert _vllm(handler).classify(INPUT_PROMPT, "turn", INPUT_SCHEMA) is None


def test_vllm_classify_returns_none_on_unparseable_content() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": "not json"}}]})

    assert _vllm(handler).classify(INPUT_PROMPT, "turn", INPUT_SCHEMA) is None


def test_classify_can_use_a_smaller_model_than_the_answer() -> None:
    """Screening sits on the request path of every turn and is a shallow
    judgement, so it is allowed its own model."""
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"findings": []}'}}]})

    _vllm(handler).classify(INPUT_PROMPT, "turn", INPUT_SCHEMA, model="small-guard")
    assert seen["model"] == "small-guard"


def test_deterministic_provider_declines_to_judge() -> None:
    """No model, so no verdict — the rule layer stands on its own rather than
    being handed a fabricated one."""
    assert DeterministicProvider().classify(INPUT_PROMPT, "turn", INPUT_SCHEMA) is None
