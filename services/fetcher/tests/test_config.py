"""Tests for fetcher.config."""

import pytest
from fetcher.config import Settings


def test_settings_loads_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """With no env vars set, defaults are used and required envs are supplied."""
    for var in ["FETCHER_DB_PATH", "FETCHER_CACHE_TTL_DAYS", "FETCHER_BATCH_MAX"]:
        monkeypatch.delenv(var, raising=False)

    monkeypatch.setenv("FETCHER_JINA_API_KEY", "test-jina-key")
    monkeypatch.setenv("FETCHER_SOCKS5_URL", "socks5://127.0.0.1:1080")
    monkeypatch.setenv("FETCHER_LLAMA_PARSE_API_KEY", "test-llama-key")

    settings = Settings()

    assert settings.db_path == "/app/data/fetcher.db"
    assert settings.cache_ttl_days == 365
    assert settings.batch_max == 100
    assert settings.default_timeout_s == 30
    assert settings.jina_api_key == "test-jina-key"
    assert settings.socks5_url == "socks5://127.0.0.1:1080"
    assert settings.llama_parse_api_key == "test-llama-key"
    assert settings.llama_parse_tier_arxiv == "agentic_plus"


def test_settings_raises_when_required_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Missing FETCHER_JINA_API_KEY raises a ValidationError."""
    monkeypatch.delenv("FETCHER_JINA_API_KEY", raising=False)
    monkeypatch.setenv("FETCHER_SOCKS5_URL", "socks5://x")
    monkeypatch.setenv("FETCHER_LLAMA_PARSE_API_KEY", "x")

    with pytest.raises(Exception) as excinfo:
        Settings()

    error = str(excinfo.value)
    assert "FETCHER_JINA_API_KEY" in error or "jina_api_key" in error.lower()
