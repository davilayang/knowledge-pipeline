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

COPY packages/domains/src/ packages/domains/src/
COPY packages/workflows/src/ packages/workflows/src/
COPY packages/retrievers/src/ packages/retrievers/src/
COPY packages/orchestrators/src/ packages/orchestrators/src/

# Install workspace packages (editable) after source is copied.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --package knowledge-orchestrators

# Drop root before runtime.
# APP_UID/APP_GID are passed from compose so the in-container user matches
# the host deploy user that owns bind-mounted dirs (./data, ./logs, etc.).
# With matching uid/gid no host-side chown gymnastics are needed.
# /home/dagster is created explicitly (not via useradd --create-home, which
# would pull in skel dotfiles) so libs that cache to $HOME (langfuse, hf,
# etc.) have a writable target without falling back to "/".
ARG APP_UID=1001
ARG APP_GID=1001
# /opt/dagster/home/storage is the fs_io_manager target — the path lives in
# the daemon/webserver's DAGSTER_HOME and is sent to dagster-code over gRPC
# when assets persist their outputs. dagster-code's image doesn't define
# DAGSTER_HOME, so we just create the path with the right owner so the
# pickle write at runtime succeeds.
RUN groupadd --gid ${APP_GID} dagster \
    && useradd --uid ${APP_UID} --gid ${APP_GID} --no-create-home --home-dir /home/dagster --shell /usr/sbin/nologin dagster \
    && mkdir -p /home/dagster /opt/dagster/home/storage \
    && chown -R dagster:dagster /app /home/dagster /opt/dagster
ENV HOME=/home/dagster
USER dagster

EXPOSE 4000

HEALTHCHECK --timeout=5s --start-period=30s --interval=5s --retries=5 \
    CMD ["/app/.venv/bin/dagster", "api", "grpc-health-check", "-p", "4000"]

# Invoke the binary from the prebuilt venv directly. `uv run` would re-sync the
# workspace at startup (defaults to all packages, dev deps included), adding
# ~13 s to startup.
CMD ["/app/.venv/bin/dagster", "code-server", "start", \
     "-h", "0.0.0.0", "-p", "4000", \
     "-m", "orchestrators.definitions"]
