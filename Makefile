.PHONY: install lint typecheck test schemas ci eval up down migrate

install:
	uv sync

lint:
	uv run ruff check .
	uv run ruff format --check .

typecheck:
	uv run mypy

test:
	uv run pytest

schemas:
	uv run python -m contracts.export_schemas

ci: lint typecheck test

# Requires the compose stack (make up) — Phase 0 smoke suite.
eval:
	uv run python evals/runner.py --suite evals/golden/smoke.yaml

up:
	docker compose -f infra/docker-compose.yml up -d --build postgres redis migrate gateway orchestrator retrieval

down:
	docker compose -f infra/docker-compose.yml down

migrate:
	cd db && uv run alembic upgrade head
