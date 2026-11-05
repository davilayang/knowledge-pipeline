# syntax=docker/dockerfile:1.7
# Search service: POST /v1/search. Slim Python base; uv-built from the
# workspace lock, like the fetcher image.
FROM python:3.13-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.11.19 /uv /uvx /bin/

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app

# --locked checks uv.lock against every workspace member's manifest, so all of
# them are copied, though only searcher is installed.
COPY pyproject.toml uv.lock /app/
COPY packages/domains/pyproject.toml /app/packages/domains/pyproject.toml
COPY packages/evals/pyproject.toml /app/packages/evals/pyproject.toml
COPY packages/orchestrators/pyproject.toml /app/packages/orchestrators/pyproject.toml
COPY packages/retrievers/pyproject.toml /app/packages/retrievers/pyproject.toml
COPY packages/workflows/pyproject.toml /app/packages/workflows/pyproject.toml
COPY services/fetcher/pyproject.toml /app/services/fetcher/pyproject.toml
COPY services/searcher/pyproject.toml /app/services/searcher/pyproject.toml
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --package searcher --no-install-workspace --no-dev --no-editable

COPY services/searcher/src /app/services/searcher/src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --package searcher --no-dev --no-editable


FROM python:3.13-slim AS runtime

RUN useradd --create-home --uid 1000 searcher

COPY --from=builder --chown=searcher:searcher /app/.venv /app/.venv

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

USER searcher
WORKDIR /app

EXPOSE 8000

CMD ["uvicorn", "searcher.app:app", "--host", "0.0.0.0", "--port", "8000"]
