"""Shared pytest fixtures.

`wiki_pg` yields a fresh psycopg connection to a temporary Postgres instance
managed by pytest-postgresql. The wiki schema is loaded once per process at
fixture startup; each test gets an empty database (pytest-postgresql truncates
between tests). Use this for any test that exercises domains.wiki.state or
the wiki_synthesis workflow.
"""

from pathlib import Path

from pytest_postgresql import factories

_REPO_ROOT = Path(__file__).resolve().parents[1]
_WIKI_SCHEMA = _REPO_ROOT / "packages/domains/src/domains/wiki/schema/wiki.sql"

postgresql_wiki_proc = factories.postgresql_proc(
    load=[_WIKI_SCHEMA],
)
wiki_pg = factories.postgresql("postgresql_wiki_proc")
