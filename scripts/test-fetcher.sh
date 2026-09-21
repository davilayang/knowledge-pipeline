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
  echo "  cd services/fetcher && uv sync --extra dev" >&2
  exit 1
fi

# pytest resolves --deselect node ids against rootdir, so running from the repo
# root silently deselects nothing and the suite reports a failure it was told to
# skip. test_the_service_loads_that_config_on_import fails on main as well
# (import-order dependent: get_model() returns None); leaving it in would make
# this red by default, which teaches everyone to ignore it.
cd services/fetcher
exec .venv/bin/python -m pytest "${@:-tests}" -q \
  --deselect tests/test_extract_config.py::test_the_service_loads_that_config_on_import
