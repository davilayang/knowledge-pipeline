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
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable


FROM python:3.13-slim AS runtime

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 1000 fetcher

COPY --from=builder --chown=fetcher:fetcher /app/.venv /app/.venv

# Runtime assets read relative to /app (the final WORKDIR). Without these the
# structurer cascade silently runs with an empty chain → "no API keys
# configured" error even when keys are present.
COPY --chown=fetcher:fetcher services/fetcher/config /app/config
COPY --chown=fetcher:fetcher services/fetcher/prompts /app/prompts

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

RUN mkdir -p /app/data && chown fetcher:fetcher /app/data

USER fetcher
WORKDIR /app

EXPOSE 8000

# Single-worker invariant: see services/fetcher/README.md.
# --root-path /fetcher: app is reverse-proxied from NA's Caddy at
# https://<tailnet>/fetcher/* (handle_path strips the prefix), so Swagger
# UI needs the prefix to generate the right openapi.json URL. Internal
# callers (http://fetcher:8000/v1/*, http://kp-fetcher:8000/v1/*) are
# unaffected — the app still listens at /.
CMD ["uvicorn", "fetcher.app:app", "--workers", "1", "--host", "0.0.0.0", "--port", "8000", "--root-path", "/fetcher"]
