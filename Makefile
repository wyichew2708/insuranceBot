.PHONY: install lint typecheck test schemas ci eval up down migrate release loadtest e2e

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

# Versioned images for every service (§10 DoD: make release).
VERSION := $(shell git describe --tags --always --dirty)
SERVICES := gateway orchestrator retrieval ingestion crawler
release:
	@for svc in $(SERVICES); do \
		docker build -f infra/containerfiles/$$svc.Containerfile \
			-t insurancebot-$$svc:$(VERSION) -t insurancebot-$$svc:latest . || exit 1; \
	done
	@echo "built images at version $(VERSION)"

loadtest:
	uv run --with locust locust -f infra/loadtest/locustfile.py \
		--host http://localhost:8000 --users 50 --spawn-rate 10 \
		--run-time 3m --headless --csv .eval-reports/loadtest

# Full local e2e: stack up, migrate, ingest fixture bundle (pseudo-embeddings
# when no embed endpoint is configured), then run the smoke suite.
e2e: up
	uv run python -m ingestion.cli ingest --bundle-path evals/fixture-bundle
	uv run python evals/runner.py --suite evals/golden/smoke.yaml
