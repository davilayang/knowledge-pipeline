# syntax=docker/dockerfile:1.7
# Fetcher service - Phase 0 skeleton. Slim Python base; uv-built.
FROM python:3.13-slim AS builder

# Official uv Docker integration recommends copying uv from the published image,
# pinning the tag, and using cache mounts for sync.
COPY --from=ghcr.io/astral-sh/uv:0.11.19 /uv /uvx /bin/

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app/services/fetcher

# Copy manifests first for dependency-layer caching. Keep paths aligned with
# the source tree so the local knowledge-domains path dependency resolves.
COPY services/fetcher/pyproject.toml services/fetcher/uv.lock /app/services/fetcher/
COPY packages/domains/pyproject.toml /app/packages/domains/pyproject.toml
COPY packages/domains/src /app/packages/domains/src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-install-project --no-dev --no-editable

COPY services/fetcher/src /app/services/fetcher/src
COPY services/fetcher/config /app/services/fetcher/config
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable


FROM python:3.13-slim AS runtime

RUN useradd --create-home --uid 1000 fetcher

COPY --from=builder --chown=fetcher:fetcher /app/.venv /app/.venv
# Ship runtime-data config (medium_domains.yaml etc.) alongside the venv.
# `medium.py` reads FETCHER_MEDIUM_DOMAINS_PATH (set below) at import time
# and now fails fast if the file is missing or empty.
COPY --from=builder --chown=fetcher:fetcher /app/services/fetcher/config /app/services/fetcher/config

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    FETCHER_MEDIUM_DOMAINS_PATH=/app/services/fetcher/config/medium_domains.yaml

RUN mkdir -p /app/data && chown fetcher:fetcher /app/data

USER fetcher
WORKDIR /app

EXPOSE 8000

# Single-worker invariant: see services/fetcher/README.md.
CMD ["uvicorn", "fetcher.app:app", "--workers", "1", "--host", "0.0.0.0", "--port", "8000"]
