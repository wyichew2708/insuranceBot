FROM python:3.11-slim
RUN useradd --uid 10001 --create-home appuser
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY packages ./packages
COPY apps ./apps
RUN uv sync --frozen --no-dev --package analytics
ENV PATH="/app/.venv/bin:$PATH"
USER 10001
CMD python -m analytics.gap_report --out /reports/gap_report.csv
