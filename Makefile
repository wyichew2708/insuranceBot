.PHONY: install dev lint typecheck test lint-bundle conflicts evals autoeval autoeval-generate \
        crawl crawl-fixture wiki knowledge autoeval-web ci console clean

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

# Loop 1 (upstream of everything) — gather the sources.
# The real run; only the allowlisted hosts are ever contacted, robots.txt is
# obeyed, and snapshots land dated under okf/raw/web/<host>/<date>/.
crawl:
	uv run python -m crawler.cli run --allowlist www.etiqa.com.sg www.tiq.com.sg --out okf/raw --rps 1.0

# The same crawler against an in-process synthetic site — no network at all.
# This is what runs in CI and what the compile tests are built on.
crawl-fixture:
	uv run python -m crawler.cli run --allowlist www.etiqa.example www.tiq.example \
		--out okf-web/raw --rps 200 --fixture

# Loop 2 — compile the snapshots into the wiki. Pages land as `draft`; the
# sign-off is what a human review records, and only approved pages are served.
wiki:
	uv run python -m compiler.cli --bundle okf-web wiki

knowledge: crawl-fixture
	uv run python -m compiler.cli --bundle okf-web wiki \
		--sign-off "product-owner:compile-run" "compliance:compile-run"
	uv run python -m compiler.cli --bundle okf-web lint

autoeval-web:
	uv run python -m evalgen.cli --bundle okf-web --out .eval-reports/web --gate 0.95 all

# Loop 2 — the knowledge gates.
lint-bundle:
	uv run python scripts/lint_bundle.py

conflicts:
	uv run python -m compiler.cli conflicts

# Loop 3 — blocks promotion on any regression.
evals:
	uv run python -m evals.runner --gate 1.0

# Auto-evaluation: derive FAQ pairs from the corpus, run them, score, report.
# Writes .eval-reports/auto-eval.{json,md,html}.
autoeval:
	uv run python -m evalgen.cli all --gate 0.95

autoeval-generate:
	uv run python -m evalgen.cli generate

ci: lint typecheck test lint-bundle evals autoeval knowledge autoeval-web

clean:
	rm -rf .mypy_cache .ruff_cache .pytest_cache
