"""Tests for fetcher.cache."""

import hashlib
from pathlib import Path

import pytest

from domains.fetches_store.sources import create_schema

from fetcher.cache import cache_key, compute_etag, lookup, upsert
from fetcher.db import open_connection


@pytest.fixture
def conn(tmp_db_path: str):
    create_schema(db_path=Path(tmp_db_path))
    connection = open_connection(tmp_db_path)
    yield connection
    connection.close()


def test_cache_key_is_sha256_of_canonical() -> None:
    assert (
        cache_key("https://example.com/x") == hashlib.sha256(b"https://example.com/x").hexdigest()
    )


def test_compute_etag_is_sha256_of_markdown() -> None:
    assert compute_etag("hello") == hashlib.sha256(b"hello").hexdigest()


def test_upsert_then_lookup_hit(conn) -> None:
    upsert(
        conn,
        canonical_url="https://example.com/x",
        source_type="article",
        markdown="# hi\n\nbody",
        tier_used="jina",
        metadata={"title": "hi"},
        tier_log=[],
        ttl_days=365,
    )

    row = lookup(conn, "https://example.com/x")
    assert row is not None
    assert row.markdown == "# hi\n\nbody"
    assert row.tier_used == "jina"
    assert row.source_type == "article"
    assert row.metadata == {"title": "hi"}
    assert row.content_chars == len("# hi\n\nbody")


def test_lookup_miss_returns_none(conn) -> None:
    assert lookup(conn, "https://nothing.com") is None


def test_upsert_overwrites_prior_row(conn) -> None:
    upsert(
        conn,
        canonical_url="https://x",
        source_type="article",
        markdown="first",
        tier_used="jina",
        metadata={},
        tier_log=[],
        ttl_days=365,
    )
    upsert(
        conn,
        canonical_url="https://x",
        source_type="article",
        markdown="second",
        tier_used="curl_cffi",
        metadata={},
        tier_log=[],
        ttl_days=365,
    )
    row = lookup(conn, "https://x")
    assert row is not None
    assert row.markdown == "second"
    assert row.tier_used == "curl_cffi"


def test_expired_row_lookup_returns_none_and_deletes(conn) -> None:
    upsert(
        conn,
        canonical_url="https://x",
        source_type="article",
        markdown="m",
        tier_used="jina",
        metadata={},
        tier_log=[],
        ttl_days=-1,
    )
    assert lookup(conn, "https://x") is None
    assert conn.execute("SELECT COUNT(*) FROM cache").fetchone()[0] == 0
