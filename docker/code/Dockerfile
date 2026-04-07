# Code location image — contains project code and all dependencies.
# Runs a gRPC code server that the webserver and daemon connect to.
FROM python:3.13-slim

# Install uv for fast dependency resolution
RUN pip install --no-cache-dir uv

WORKDIR /app

# Copy dependency files first for layer caching
COPY pyproject.toml uv.lock ./

# Install dependencies (without dev group)
ENV UV_PROJECT_ENVIRONMENT=/app/.venv
RUN uv sync --frozen --no-dev --no-install-project

# Copy project source
COPY src/ src/

# Install the project itself
RUN uv sync --frozen --no-dev

EXPOSE 4000

HEALTHCHECK --timeout=1s --start-period=30s --interval=5s --retries=20 \
    CMD ["/app/.venv/bin/dagster", "api", "grpc-health-check", "-p", "4000"]

CMD ["uv", "run", "dagster", "code-server", "start", "-h", "0.0.0.0", "-p", "4000", "-m", "knowledge_pipeline.definitions"]
