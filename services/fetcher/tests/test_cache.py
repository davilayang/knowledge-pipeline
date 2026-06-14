"""Tests for fetcher.cache."""

import hashlib
from pathlib import Path

import pytest

from domains.fetches_store.sources import _connect, create_schema
from fetcher.cache import cache_key, compute_etag, lookup, upsert


@pytest.fixture
def db_path(tmp_db_path: str) -> Path:
    path = Path(tmp_db_path)
    create_schema(db_path=path)
    return path


def test_cache_key_is_sha256_of_canonical() -> None:
    assert (
        cache_key("https://example.com/x") == hashlib.sha256(b"https://example.com/x").hexdigest()
    )


def test_compute_etag_is_sha256_of_markdown() -> None:
    assert compute_etag("hello") == hashlib.sha256(b"hello").hexdigest()


def test_upsert_then_lookup_hit(db_path: Path) -> None:
    upsert(
        db_path=db_path,
        canonical_url="https://example.com/x",
        source_type="article",
        markdown="# hi\n\nbody",
        tier_used="jina",
        metadata={"title": "hi"},
        tier_log=[],
        ttl_days=365,
    )

    row = lookup(db_path=db_path, canonical_url="https://example.com/x")
    # markdown, tier_used, source_type, metadata are pass-through from upsert;
    # content_chars is the only DB-computed side effect worth pinning.
    assert row is not None
    assert row.content_chars == len("# hi\n\nbody")


def test_lookup_miss_returns_none(db_path: Path) -> None:
    assert lookup(db_path=db_path, canonical_url="https://nothing.com") is None


def test_upsert_overwrites_prior_row(db_path: Path) -> None:
    upsert(
        db_path=db_path,
        canonical_url="https://x",
        source_type="article",
        markdown="first",
        tier_used="jina",
        metadata={},
        tier_log=[],
        ttl_days=365,
    )
    upsert(
        db_path=db_path,
        canonical_url="https://x",
        source_type="article",
        markdown="second",
        tier_used="curl_cffi",
        metadata={},
        tier_log=[],
        ttl_days=365,
    )
    row = lookup(db_path=db_path, canonical_url="https://x")
    assert row is not None
    assert row.markdown == "second"
    assert row.tier_used == "curl_cffi"


def test_expired_row_lookup_returns_none(db_path: Path) -> None:
    """Caller-visible: lookup of an expired row returns None (not stale data)."""
    upsert(
        db_path=db_path,
        canonical_url="https://x",
        source_type="article",
        markdown="m",
        tier_used="jina",
        metadata={},
        tier_log=[],
        ttl_days=-1,
    )
    assert lookup(db_path=db_path, canonical_url="https://x") is None


def test_expired_row_is_physically_deleted_on_lookup(db_path: Path) -> None:
    """GC side effect: expired rows are removed from the table on access, so
    the table doesn't accumulate stale entries. Independent of the None-return
    contract — an implementation could satisfy that without ever pruning."""
    upsert(
        db_path=db_path,
        canonical_url="https://x",
        source_type="article",
        markdown="m",
        tier_used="jina",
        metadata={},
        tier_log=[],
        ttl_days=-1,
    )
    lookup(db_path=db_path, canonical_url="https://x")

    with _connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM cache").fetchone()[0] == 0
