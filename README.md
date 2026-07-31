# insuranceBot

GenAI insurance chatbot for internal and external enquiries plus policy-servicing
guidance, for two brands (etiqa.com.sg / tiq.com.sg) of one MAS-regulated insurer.
See `plan.md` for the full development plan; this README covers running v1.

## Layout

```
apps/          gateway · orchestrator · retrieval · ingestion · crawler · widget · portal
packages/      contracts (models + settings + JSON Schemas) · clients (vLLM, Langfuse, …)
evals/         golden suites, graders, fixture bundle, regression runner
db/            alembic migrations (pgvector)
infra/         docker-compose, Containerfiles (rootless), nginx sample
```

## Quick start (dev)

```bash
uv sync                 # install workspace (Python 3.11+)
make ci                 # ruff + mypy + pytest
make up                 # postgres(pgvector) + redis + migrations + services
make eval               # smoke suite against the running gateway
```

Copy `.env.example` to `.env` and fill in the externally hosted vLLM endpoints
(agent / judge / embed / rerank) and, when available, the CMS bundle git URL.
LLM serving and the CMS are **not** part of this repo — they are consumed via config.

## Ingesting the dev fixture bundle

```bash
uv run python -m ingestion.cli lint   --bundle-path evals/fixture-bundle
uv run python -m ingestion.cli ingest --bundle-path evals/fixture-bundle
uv run python -m ingestion.cli rollback <bundle_id>
```

## Status

Phase 0 (foundations) is complete; testable cores of Phases 1–4 are in place
(chunker, validators, filter SQL, graders, router, crawler URL policy). The
agent planner is a stub — see `plan.md` Phase 3 for what lands next.
