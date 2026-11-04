#!/usr/bin/env bash
# Run the fetcher service's own test suite.
#
# Usage:
#   ./scripts/test-fetcher.sh                # the whole suite
#   ./scripts/test-fetcher.sh tests/cache    # a subset, paths relative to services/fetcher
#
# The service is not a uv workspace member — its own venv, its own lockfile, and
# `fastapi` is absent from the workspace venv — so the root `test` task cannot
# reach these tests and they need this venv to run at all.
set -euo pipefail

VENV="services/fetcher/.venv"
if [ ! -x "$VENV/bin/python" ]; then
  echo "$VENV is missing. Create it with:" >&2
  echo "  cd services/fetcher && uv sync --locked --extra dev" >&2
  exit 1
fi

# Run from services/fetcher: the service reads config/extraction.yaml relative
# to its working directory, as the container does from /app, and
# test_the_service_loads_that_config_on_import checks exactly that path.
cd services/fetcher
exec .venv/bin/python -m pytest "${@:-tests}" -q
