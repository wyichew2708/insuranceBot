"""Configuration. Every external dependency is optional: with nothing set the
serve loop runs fully deterministically, which is what makes the debug console
and the eval suites usable offline."""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    bundle_path: Path = Path("okf")

    # Optional vLLM endpoints (§H.1). Unset => deterministic composer.
    vllm_base_url: str = ""
    vllm_model: str = ""
    vllm_api_key: str = ""

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
