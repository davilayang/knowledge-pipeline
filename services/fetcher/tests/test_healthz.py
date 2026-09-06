"""Tests for the FastAPI app skeleton and /healthz endpoint."""

import sqlite3

import pytest
from fastapi.testclient import TestClient
from fetcher.app import create_app


def test_healthz_returns_200_when_ready(
    tmp_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """/healthz returns 200 with ok=true when required config is present.
    The registered-handlers list shape is tested in test_registry.py."""
    monkeypatch.setenv("FETCHER_DB_PATH", tmp_db_path)
    monkeypatch.setenv("FETCHER_JINA_API_KEY", "x")
    monkeypatch.setenv("FETCHER_SOCKS5_URL", "socks5://x")
    monkeypatch.setenv("FETCHER_LLAMA_PARSE_API_KEY", "x")

    app = create_app()
    with TestClient(app) as client:
        response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json()["ok"] is True


def test_healthz_returns_503_when_required_env_missing(
    tmp_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing FETCHER_SOCKS5_URL returns 503 with ok=false and missing list."""
    monkeypatch.setenv("FETCHER_DB_PATH", tmp_db_path)
    monkeypatch.delenv("FETCHER_SOCKS5_URL", raising=False)
    monkeypatch.setenv("FETCHER_JINA_API_KEY", "x")
    monkeypatch.setenv("FETCHER_LLAMA_PARSE_API_KEY", "x")

    app = create_app()
    with TestClient(app) as client:
        response = client.get("/healthz")

    assert response.status_code == 503
    body = response.json()
    assert body["ok"] is False
    assert any("socks5" in missing.lower() for missing in body["missing"])


def test_app_init_creates_schema(
    tmp_db_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """App startup runs init_schema on the configured DB."""
    monkeypatch.setenv("FETCHER_DB_PATH", tmp_db_path)
    monkeypatch.setenv("FETCHER_JINA_API_KEY", "x")
    monkeypatch.setenv("FETCHER_SOCKS5_URL", "socks5://x")
    monkeypatch.setenv("FETCHER_LLAMA_PARSE_API_KEY", "x")

    app = create_app()
    with TestClient(app) as client:
        client.get("/healthz")

    conn = sqlite3.connect(tmp_db_path)
    try:
        names = [
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            ).fetchall()
        ]
        assert names == ["cache", "extraction_cache", "fetches", "url_aliases"]
    finally:
        conn.close()
