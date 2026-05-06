FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /app

# rclone — for DAG "backup-readings" to sync with Google Drive.
ARG TARGETARCH
ARG RCLONE_VERSION=1.74.0
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl unzip \
    && curl -fsSL "https://downloads.rclone.org/v${RCLONE_VERSION}/rclone-v${RCLONE_VERSION}-linux-${TARGETARCH:-amd64}.zip" -o /tmp/rclone.zip \
    && unzip -j /tmp/rclone.zip "*/rclone" -d /usr/local/bin \
    && rm /tmp/rclone.zip \
    && apt-get purge -y --auto-remove curl unzip \
    && rm -rf /var/lib/apt/lists/*

# Install dependencies — this layer is cached until pyproject.toml / uv.lock change.
# uv needs all workspace member manifests for resolution even when only installing
# a subset — retrievers/evals pyproject.toml are copied for graph resolution only,
# their source and deps are not installed (they're optional extras in orchestrators).
COPY pyproject.toml uv.lock ./

COPY packages/domains/pyproject.toml packages/domains/pyproject.toml
COPY packages/workflows/pyproject.toml packages/workflows/pyproject.toml
COPY packages/retrievers/pyproject.toml packages/retrievers/pyproject.toml
COPY packages/evals/pyproject.toml packages/evals/pyproject.toml
COPY packages/orchestrators/pyproject.toml packages/orchestrators/pyproject.toml

ENV UV_PROJECT_ENVIRONMENT=/app/.venv
# UV_LINK_MODE=copy: copy wheels from the BuildKit cache mount into the venv
# instead of hardlinking (hardlinks across mount boundaries fail).
ENV UV_LINK_MODE=copy
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-workspace --package knowledge-orchestrators

# Copy source for production workspace members only (domains + workflows + orchestrators).
# retrievers and evals are workbench-only optional extras — excluded from this image
# to avoid pulling in sentence-transformers/PyTorch/chromadb/ragas (~8GB of ML deps).
COPY packages/domains/src/ packages/domains/src/
COPY packages/workflows/src/ packages/workflows/src/
COPY packages/orchestrators/src/ packages/orchestrators/src/

# Install workspace packages (editable) after source is copied.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --package knowledge-orchestrators

# Drop root before runtime.
# Bind-mounted host directories (./data, ./datasets) must be writable by uid 1001
# — see docker-compose.yml. Adjust host permissions if you see permission denied at runtime.
# /home/dagster is created explicitly (not via useradd --create-home, which would
# pull in skel dotfiles) so libs that cache to $HOME (langfuse, hf, etc.) have a
# writable target without falling back to "/" or failing outright.
RUN useradd --system --uid 1001 --no-create-home --home-dir /home/dagster --shell /usr/sbin/nologin dagster \
    && mkdir -p /home/dagster \
    && chown -R dagster:dagster /app /home/dagster
ENV HOME=/home/dagster
USER dagster

EXPOSE 4000

HEALTHCHECK --timeout=5s --start-period=30s --interval=5s --retries=5 \
    CMD ["/app/.venv/bin/dagster", "api", "grpc-health-check", "-p", "4000"]

# Load pipelines-only code location (backup + wiki). The full orchestrators.definitions
# merger also includes the RAG workbench, which imports from retrievers/evals — packages
# not installed in this image. Run workbench jobs locally via `poe dagster-dev`.
# Invoke the binary from the prebuilt venv directly. `uv run` would re-sync the
# workspace at startup (defaults to all packages, dev deps included), pulling in
# the exact extras we excluded at build time and adding ~13 s to startup.
CMD ["/app/.venv/bin/dagster", "code-server", "start", \
     "-h", "0.0.0.0", "-p", "4000", \
     "-m", "orchestrators.defs.pipelines.definitions"]
