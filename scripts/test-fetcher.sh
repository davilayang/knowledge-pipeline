#!/usr/bin/env bash
# Run the fetcher service's test suite.
#
# Usage:
#   ./scripts/test-fetcher.sh                # the whole suite
#   ./scripts/test-fetcher.sh tests/cache    # a subset, paths relative to services/fetcher
#
# The fetcher is a workspace member and shares the root venv, but its suite is a
# separate pytest run: both suites have a top-level tests/__init__.py, so one
# collection would clash on the package name.
set -euo pipefail

# Run from services/fetcher: the service reads config/extraction.yaml relative
# to its working directory, as the container does from /app, and
# test_the_service_loads_that_config_on_import checks exactly that path. It also
# makes pytest pick up the fetcher's own [tool.pytest.ini_options].
cd "$(dirname "$0")/../services/fetcher"
exec uv run --locked python -m pytest "${@:-tests}" -q
