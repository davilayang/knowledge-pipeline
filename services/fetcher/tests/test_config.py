"""Tests for fetcher.config."""

import pytest
from fetcher.config import Settings


def test_settings_loads_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """Settings construction with required envs succeeds. The two load-bearing
    defaults (db_path → governs cache location; cache_ttl_days → governs
    eviction) are pinned; the rest of the field values are enforced by the
    Pydantic type annotations themselves."""
    for var in ["FETCHER_DB_PATH", "FETCHER_CACHE_TTL_DAYS", "FETCHER_BATCH_MAX"]:
        monkeypatch.delenv(var, raising=False)

    monkeypatch.setenv("FETCHER_JINA_API_KEY", "test-jina-key")
    monkeypatch.setenv("FETCHER_SOCKS5_URL", "socks5://127.0.0.1:1080")
    monkeypatch.setenv("FETCHER_LLAMA_PARSE_API_KEY", "test-llama-key")

    settings = Settings()

    assert settings.db_path == "/app/data/fetches.db"
    assert settings.cache_ttl_days == 365


def test_settings_raises_when_required_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Missing FETCHER_SOCKS5_URL raises a ValidationError (jina key is optional)."""
    monkeypatch.delenv("FETCHER_SOCKS5_URL", raising=False)
    monkeypatch.setenv("FETCHER_JINA_API_KEY", "x")
    monkeypatch.setenv("FETCHER_LLAMA_PARSE_API_KEY", "x")

    with pytest.raises(Exception) as excinfo:
        Settings()

    error = str(excinfo.value)
    assert "FETCHER_SOCKS5_URL" in error or "socks5_url" in error.lower()


def test_settings_accepts_missing_jina_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Jina key is optional — free tier works without auth."""
    monkeypatch.delenv("FETCHER_JINA_API_KEY", raising=False)
    monkeypatch.setenv("FETCHER_SOCKS5_URL", "socks5://x")
    monkeypatch.setenv("FETCHER_LLAMA_PARSE_API_KEY", "x")

    settings = Settings()

    assert settings.jina_api_key is None
