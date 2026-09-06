"""Tests for domains.fetches_store schema setup.

Cache, fetches, and url_aliases table operations have their own test modules
once we add direct domain-function coverage (currently exercised indirectly
through service-level integration tests).
"""

from pathlib import Path

from domains.fetches_store.sources import _connect, create_schema


def test_create_schema_creates_every_table(tmp_path: Path) -> None:
    db_path = tmp_path / "fetcher.db"
    create_schema(db_path=db_path)

    with _connect(db_path) as conn:
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
    assert tables == {"cache", "extraction_cache", "fetches", "url_aliases"}


def test_create_schema_is_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "fetcher.db"
    create_schema(db_path=db_path)
    create_schema(db_path=db_path)  # second call must not raise


def test_cache_table_has_required_columns(tmp_path: Path) -> None:
    db_path = tmp_path / "fetcher.db"
    create_schema(db_path=db_path)

    with _connect(db_path) as conn:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(cache)").fetchall()}

    required = {
        "url_hash",
        "url",
        "canonical_url",
        "source_type",
        "markdown",
        "etag",
        "tier_used",
        "content_chars",
        "metadata_json",
        "tier_log_json",
        "fetched_at",
        "expires_at",
    }
    assert required <= cols, f"missing: {required - cols}"
