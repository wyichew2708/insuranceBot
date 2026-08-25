"""Configuration. Every external dependency is optional: with nothing set the
serve loop runs fully deterministically, which is what makes the debug console
and the eval suites usable offline."""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    bundle_path: Path = Path("okf")

    # --- Generation (§H.1) ---------------------------------------------
    # Which engine phrases the answer. "auto" resolves from what is
    # configured; with nothing configured that is the deterministic
    # composer, which is what keeps CI and the eval suites offline.
    #   auto | deterministic | anthropic | vllm
    llm_provider: str = "auto"
    llm_timeout_s: float = 30.0

    # Anthropic. An empty key still works if `ant auth login` has been run —
    # the SDK resolves a stored profile.
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-5"
    # Adaptive thinking runs by default on Sonnet 5 when `thinking` is omitted,
    # and is charged against max_tokens together with the reply; low effort
    # suits a constrained rewrite.
    anthropic_effort: str = "low"
    anthropic_max_tokens: int = 8192

    # A locally hosted model over vLLM's OpenAI-compatible route.
    vllm_base_url: str = ""
    vllm_model: str = ""
    vllm_api_key: str = ""
    vllm_max_tokens: int = 1024

    # --- Guardrails (§F.4) ----------------------------------------------
    # A second screen either side of the loop, complementing the deterministic
    # gates with a semantic one. The rule layer always runs and is not
    # switchable; this only decides whether a model reads the turn as well.
    #   auto | rules | off
    guardrails: str = "auto"
    # Screening shares the answering provider and its credentials — there is no
    # separate guardrail key — so a configured answer model is a configured
    # screen. That also means a fully configured turn makes up to three calls:
    # screen the question, write the answer, screen the answer. This overrides
    # only the model for the two screening calls. Screening is a shallow
    # judgement on the request path of every turn, so pointing it at a smaller
    # model (claude-haiku-4-5-20251001) is usually the right trade against the
    # model writing the answer.
    guardrail_model: str = ""
    guardrail_max_tokens: int = 512
    # What an unscreened turn is worth. A configured screening model that goes
    # silent leaves the rule layer intact either way; this decides whether the
    # turn is still answered. Open by default so an outage degrades the check
    # rather than the service — regulated deployments should close it.
    guardrail_fail_closed: bool = False

    langfuse_host: str = ""
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""

    # Budgets (§F.3)
    max_pages: int = 8
    max_tool_calls: int = 6
    max_wall_clock_s: float = 10.0
    max_tokens: int = 20_000

    # Retrieval
    wiki_read_limit: int = 5
    candidate_floor: float = 0.08
    confidence_floor: float = 0.45


def get_settings() -> Settings:
    return Settings()
