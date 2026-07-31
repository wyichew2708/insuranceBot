"""Single settings surface (§3). Every service reads the same env names."""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # LLM endpoints (external, OpenAI-compatible)
    vllm_agent_base_url: str = ""
    vllm_agent_model: str = ""
    vllm_judge_base_url: str = ""
    vllm_judge_model: str = ""
    vllm_embed_base_url: str = ""
    vllm_embed_model: str = ""
    vllm_rerank_base_url: str = ""
    vllm_rerank_model: str = ""
    vllm_api_key: str = ""

    # Knowledge bundle (external CMS output)
    kb_bundle_git_url: str = ""
    kb_bundle_git_ref: str = "main"
    kb_publish_stream: str = "kb.publish"

    # State
    database_url: str = ""
    redis_url: str = ""

    # Observability
    langfuse_host: str = ""
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""

    # Crawler
    crawl_allowlist: str = "www.etiqa.com.sg,www.tiq.com.sg"
    crawl_default_refresh_hours: int = 24
    crawl_promo_refresh_hours: int = 6

    # Runtime
    brands: str = "etiqa,tiq"
    agent_max_steps: int = 6
    verify_max_retries: int = 1
    session_ttl_minutes: int = 60
    eval_gate: float = Field(default=0.95, ge=0.0, le=1.0)

    # Service URLs (internal)
    orchestrator_url: str = "http://localhost:8001"
    retrieval_url: str = "http://localhost:8002"

    # Sessions & data protection (Phase 4)
    session_secret: str = ""  # empty => dev mode, no JWT enforcement
    widget_keys: str = ""  # "key1:tiq,key2:etiqa" — brand bound server-side per key
    message_enc_key: str = ""  # pgcrypto symmetric key; empty => plaintext storage
    message_ttl_days: int = 90

    @property
    def widget_key_map(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for pair in self.widget_keys.split(","):
            if ":" in pair:
                key, brand = pair.split(":", 1)
                out[key.strip()] = brand.strip()
        return out

    @property
    def allowlisted_domains(self) -> list[str]:
        return [d.strip() for d in self.crawl_allowlist.split(",") if d.strip()]

    @property
    def brand_list(self) -> list[str]:
        return [b.strip() for b in self.brands.split(",") if b.strip()]


def get_settings() -> Settings:
    return Settings()
