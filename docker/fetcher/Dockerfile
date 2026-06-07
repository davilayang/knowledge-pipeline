# syntax=docker/dockerfile:1.7
# Fetcher service - Phase 0 skeleton. Slim Python base; uv-built.
FROM python:3.13-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

COPY services/fetcher/pyproject.toml services/fetcher/uv.lock /app/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

COPY services/fetcher/src /app/src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev


FROM python:3.13-slim AS runtime

RUN useradd --create-home --uid 1000 fetcher

COPY --from=builder --chown=fetcher:fetcher /app/.venv /app/.venv
COPY --from=builder --chown=fetcher:fetcher /app/src /app/src

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH="/app/src" \
    PYTHONUNBUFFERED=1

RUN mkdir -p /app/data && chown fetcher:fetcher /app/data

USER fetcher
WORKDIR /app

EXPOSE 8000

# Single-worker invariant: see services/fetcher/README.md.
CMD ["uvicorn", "fetcher.app:app", "--workers", "1", "--host", "0.0.0.0", "--port", "8000"]
