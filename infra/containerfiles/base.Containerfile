# Shared pattern for all Python services: rootless-podman compatible,
# non-root UID, no privileged ports, explicit writable dirs.
# Each service Containerfile follows this exact structure.
FROM python:3.11-slim AS base
RUN useradd --uid 10001 --create-home appuser
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY packages ./packages
COPY apps ./apps
RUN uv sync --frozen --no-dev
USER 10001
