"""Shared pytest fixtures.

`wiki_pg` yields a fresh psycopg connection to a temporary Postgres instance
managed by pytest-postgresql. The wiki schema is loaded once per process at
fixture startup; each test gets an empty database (pytest-postgresql truncates
between tests). Use this for any test that exercises domains.wiki.state.

`wiki_pg_url` returns the connection URL for code paths (like the workflow
nodes) that open their own short-lived connections from a string.
"""

from pathlib import Path

import pytest
from pytest_postgresql import factories

_REPO_ROOT = Path(__file__).resolve().parents[1]
_WIKI_SCHEMA = _REPO_ROOT / "packages/domains/src/domains/wiki/schema/wiki.sql"

postgresql_wiki_proc = factories.postgresql_proc(
    load=[_WIKI_SCHEMA],
)
wiki_pg = factories.postgresql("postgresql_wiki_proc")


@pytest.fixture
def wiki_pg_url(wiki_pg) -> str:
    """Build a postgresql:// URL pointing at the same DB as wiki_pg.

    Workflow nodes open their own connections from a URL string (so each node
    is independent and the workflow can be invoked from any process). This
    fixture surfaces that URL while the wiki_pg fixture keeps the test's own
    inspection connection.
    """
    info = wiki_pg.info
    return f"postgresql://{info.user}@{info.host}:{info.port}/{info.dbname}"
