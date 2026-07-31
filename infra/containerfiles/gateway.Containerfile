FROM python:3.11-slim
RUN useradd --uid 10001 --create-home appuser
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY packages ./packages
COPY apps ./apps
RUN uv sync --frozen --no-dev --package gateway
ENV PATH="/app/.venv/bin:$PATH"
USER 10001
EXPOSE 8000
CMD uvicorn gateway.main:app --host 0.0.0.0 --port 8000
