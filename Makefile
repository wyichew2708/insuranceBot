.PHONY: install dev lint typecheck test lint-bundle conflicts evals ci console clean

install:
	uv sync

# The console is the point of the dev target: start it and open the browser.
dev console:
	uv run uvicorn api.main:app --reload --port 8080

lint:
	uv run ruff check .
	uv run ruff format --check .

typecheck:
	uv run mypy

test:
	uv run pytest

# Loop 2 — the knowledge gates.
lint-bundle:
	uv run python scripts/lint_bundle.py

conflicts:
	uv run python -m compiler.cli conflicts

# Loop 3 — blocks promotion on any regression.
evals:
	uv run python -m evals.runner --gate 1.0

ci: lint typecheck test lint-bundle evals

clean:
	rm -rf .mypy_cache .ruff_cache .pytest_cache
