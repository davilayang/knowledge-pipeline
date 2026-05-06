# Code location image — contains project code and all dependencies.
# Runs a gRPC code server that the webserver and daemon connect to.

# Refresh ~quarterly; check Python release notes before bumping.
FROM python:3.13.13-slim

# Refresh alongside Python image; check https://github.com/astral-sh/uv/releases.
COPY --from=ghcr.io/astral-sh/uv:0.11.10 /uv /uvx /usr/local/bin/

# rclone — needed for the backup pipeline's Drive offload assets.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl unzip \
    && curl -fsSL https://rclone.org/install.sh | bash \
    && apt-get purge -y --auto-remove curl unzip \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install dependencies — this layer is cached until pyproject.toml / uv.lock change.
# Copy workspace manifests needed to resolve knowledge-orchestrators.
COPY pyproject.toml uv.lock ./

COPY packages/domains/pyproject.toml packages/domains/pyproject.toml
COPY packages/workflows/pyproject.toml packages/workflows/pyproject.toml
COPY packages/retrievers/pyproject.toml packages/retrievers/pyproject.toml
COPY packages/evals/pyproject.toml packages/evals/pyproject.toml
COPY packages/orchestrators/pyproject.toml packages/orchestrators/pyproject.toml

ENV UV_PROJECT_ENVIRONMENT=/app/.venv
RUN uv sync --frozen --no-dev --no-install-workspace --package knowledge-orchestrators

# Copy source for each workspace member that knowledge-orchestrators depends on.
COPY packages/domains/src/ packages/domains/src/
COPY packages/workflows/src/ packages/workflows/src/
COPY packages/retrievers/src/ packages/retrievers/src/
COPY packages/evals/src/ packages/evals/src/
COPY packages/orchestrators/src/ packages/orchestrators/src/

# Install workspace packages (editable) after source is copied.
RUN uv sync --frozen --no-dev --package knowledge-orchestrators

# Drop root before runtime.
# Bind-mounted host directories (./data, ./datasets) must be writable by uid 1001
# — see docker-compose.yml. Adjust host permissions if you see permission denied at runtime.
RUN useradd --system --uid 1001 --no-create-home --shell /usr/sbin/nologin dagster
RUN chown -R dagster:dagster /app
USER dagster

EXPOSE 4000

HEALTHCHECK --timeout=1s --start-period=30s --interval=5s --retries=20 \
    CMD ["/app/.venv/bin/dagster", "api", "grpc-health-check", "-p", "4000"]

CMD ["uv", "run", "dagster", "code-server", "start", "-h", "0.0.0.0", "-p", "4000", "-m", "orchestrators.definitions"]
