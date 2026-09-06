"""Tests for domains.fetch_store schema setup.

fetch_cache, async_jobs, and url_aliases table operations have their own test modules
once we add direct domain-function coverage (currently exercised indirectly
through service-level integration tests).
"""

from pathlib import Path

from domains.fetch_store.sources import _connect, create_schema, get_job, insert_job


def test_create_schema_creates_three_tables(tmp_path: Path) -> None:
    db_path = tmp_path / "fetcher.db"
    create_schema(db_path=db_path)

    with _connect(db_path) as conn:
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
    assert tables == {"fetch_cache", "async_jobs", "url_aliases"}


def test_create_schema_is_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "fetcher.db"
    create_schema(db_path=db_path)
    create_schema(db_path=db_path)  # second call must not raise


def test_fetch_cache_table_has_required_columns(tmp_path: Path) -> None:
    db_path = tmp_path / "fetcher.db"
    create_schema(db_path=db_path)

    with _connect(db_path) as conn:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(fetch_cache)").fetchall()}

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


def test_job_round_trips_its_job_type(tmp_path: Path) -> None:
    """async_jobs is shared by every async endpoint, so a record must carry
    which kind of job it is."""
    db_path = tmp_path / "fetcher.db"
    create_schema(db_path=db_path)

    insert_job(
        db_path=db_path,
        job_id="j1",
        batch_id="b1",
        job_type="fetch",
        request_body={"url": "https://example.com"},
    )

    assert get_job(db_path=db_path, job_id="j1")["job_type"] == "fetch"
