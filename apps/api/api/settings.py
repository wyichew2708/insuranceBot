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
    # Conversation memory: one JSON file per session under `state_dir/sessions`,
    # a one-line summary per turn. `on` writes it; `auto` is `on` in the API
    # server and `off` in tests and batch evaluation, which construct Settings
    # directly and must stay stateless; `off` relies on the client's `history`.
    #   auto | on | off
    memory: str = "auto"
    state_dir: Path = Path(".state")
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

    # Ask the model which product a question is about, instead of ranking
    # words. Off makes the turn behave exactly as it did before — the lexical
    # path is still there and still the fallback for every failure of this one.
    # Costs one extra call per turn on a configured provider, and nothing at
    # all on the deterministic one, which returns no verdict.
    resolve_with_model: bool = True

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

    # --- Retrieval: vectors (v2.1) --------------------------------------
    # Dense retrieval over pgvector, fused with the lexical rank. Recall only:
    # a chunk found by similarity is a candidate under the same frontmatter
    # filter, the same composition and the same gates as one found by words.
    # It exists because the lexical scorer ties 87-213 pages on the questions
    # customers actually ask, and no coefficient fixes a tie.
    #   auto | on | off
    # `auto` resolves from the DSN: empty means lexical only, and no stage is
    # opened. `on` fails the turn if the database is unreachable — for testing
    # the path. `off` never opens a connection.
    pgvector: str = "auto"
    pgvector_dsn: str = ""
    # An OpenAI-compatible /v1/embeddings endpoint. TEI serving bge-m3 on the
    # GPU host; the API embeds only the *question* at request time — pages are
    # embedded offline by `make index`, never on the request path.
    embed_base_url: str = ""
    embed_model: str = "BAAI/bge-m3"
    # Optional cross-encoder over the fused top-20. Off when empty.
    rerank_base_url: str = ""
    # What an unreachable database is worth. Open by default, like the
    # guardrail screen: an outage degrades recall to the lexical path rather
    # than the service — and the trace says which path served the turn.
    pgvector_fail_closed: bool = False
    # Below this cosine similarity a vector candidate is not admitted at all —
    # the dense analogue of RAG_FLOOR: a hit made of corpus-wide vocabulary is
    # the shape of every document. Calibrated on the suites, not a default.
    vector_floor: float = 0.55


def get_settings() -> Settings:
    return Settings()
