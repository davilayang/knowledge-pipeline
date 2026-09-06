"""Tests for fetcher.cache."""

import hashlib
from pathlib import Path
from unittest.mock import patch

import pytest

from domains.fetch_store.sources import _connect, create_schema
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


async def test_cache_hit_reports_current_handler_name_not_stale_source_type(db_path: Path) -> None:
    """A row cached under an old routing name returns the CURRENT handler's NAME
    on a cache hit, so a renamed handler (pdf → file_pdf) never returns a stale
    `kind` that a fresh fetch of the same URL wouldn't."""
    from unittest.mock import MagicMock, patch

    from fetcher.canonicalize import CanonicalResult
    from fetcher.fetch_service import run_fetch_request
    from fetcher.types import FetchContext, FetchRequest

    url = "https://example.com/paper.pdf"  # routes to the file_pdf handler
    # Seed the cache under the OLD source_type "pdf", content clearing the
    # pymupdf tier's fast floor (1500 chars) so the cache-hit path is taken.
    upsert(
        db_path=db_path,
        canonical_url=url,
        source_type="pdf",
        markdown="x" * 2000,
        tier_used="pymupdf4llm",
        metadata={},
        tier_log=[],
        ttl_days=365,
    )
    req = FetchRequest(url=url, quality="fast", allow_paid=False)
    with patch("fetcher.cache.canonicalize", return_value=CanonicalResult(url, url, [], [])):
        outcome = await run_fetch_request(
            req,
            db_path=db_path,
            ctx=MagicMock(spec=FetchContext),
            ttl_days=365,
            alias_ttl_days=30,
        )

    assert outcome.cache_hit is True
    assert outcome.kind == "file_pdf"  # not the stale "pdf"


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
        assert conn.execute("SELECT COUNT(*) FROM fetch_cache").fetchone()[0] == 0


def test_failed_redirect_follow_is_not_cached(tmp_path) -> None:
    """A failed redirect-follow returns the input URL echoed back, which is
    indistinguishable from a URL that redirects nowhere. Persisting it would
    pin a transient network error into the alias table for the whole TTL."""
    import httpx

    from fetcher.cache import canonicalize_cached

    db_path = tmp_path / "fetcher.db"
    create_schema(db_path=db_path)

    with patch("fetcher.canonicalize._follow_redirects", side_effect=httpx.ConnectError("boom")):
        result, cache_hit = canonicalize_cached(
            "https://example.com/x", db_path=db_path, ttl_days=30
        )

    assert result.resolved is False
    assert cache_hit is False
    with _connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM url_aliases").fetchone()[0] == 0
