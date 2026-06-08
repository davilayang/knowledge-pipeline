"""Tests for POST /v1/fetch."""

from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from fetcher.app import create_app
from fetcher.canonicalize import CanonicalResult
from fetcher.types import CascadeResult


def _setup_envs(monkeypatch, tmp_db_path: str) -> None:
    monkeypatch.setenv("FETCHER_DB_PATH", tmp_db_path)
    monkeypatch.setenv("FETCHER_JINA_API_KEY", "x")
    monkeypatch.setenv("FETCHER_SOCKS5_URL", "socks5://x")
    monkeypatch.setenv("FETCHER_LLAMA_PARSE_API_KEY", "x")


def test_fetch_returns_200_with_markdown(monkeypatch, tmp_db_path: str) -> None:
    _setup_envs(monkeypatch, tmp_db_path)
    app = create_app()
    with (
        patch("fetcher.fetch_service.canonicalize") as can_mock,
        patch("fetcher.fetch_service.run_cascade", new_callable=AsyncMock) as cascade,
    ):
        can_mock.return_value = CanonicalResult(
            "https://example.com/x", "https://example.com/x", [], []
        )
        cascade.return_value = CascadeResult("# Hello\n\nbody", "jina", [])
        with TestClient(app) as client:
            response = client.post("/v1/fetch", json={"url": "https://example.com/x"})

    assert response.status_code == 200
    body = response.json()
    assert body["markdown"] == "# Hello\n\nbody"
    assert body["source_type"] == "article"
    assert body["tier_used"] == "jina"
    assert body["cache_hit"] is False
    assert response.headers.get("etag") is not None


def test_fetch_returns_400_on_bad_url(monkeypatch, tmp_db_path: str) -> None:
    _setup_envs(monkeypatch, tmp_db_path)
    app = create_app()
    with TestClient(app) as client:
        response = client.post("/v1/fetch", json={"url": "not a url"})
    assert response.status_code == 400
    assert response.json()["code"] == "BAD_URL"


def test_fetch_returns_422_unsupported_source(monkeypatch, tmp_db_path: str) -> None:
    _setup_envs(monkeypatch, tmp_db_path)
    app = create_app()
    with TestClient(app) as client:
        response = client.post("/v1/fetch", json={"url": "https://example.com/paper.pdf"})
    assert response.status_code == 422
    assert response.json()["code"] == "UNSUPPORTED_SOURCE"


def test_fetch_cache_hit_on_second_call(monkeypatch, tmp_db_path: str) -> None:
    _setup_envs(monkeypatch, tmp_db_path)
    app = create_app()
    with (
        patch("fetcher.fetch_service.canonicalize") as can_mock,
        patch("fetcher.fetch_service.run_cascade", new_callable=AsyncMock) as cascade,
    ):
        can_mock.return_value = CanonicalResult(
            "https://example.com/x", "https://example.com/x", [], []
        )
        cascade.return_value = CascadeResult("x" * 2500, "jina", [])
        with TestClient(app) as client:
            first = client.post("/v1/fetch", json={"url": "https://example.com/x"})
            second = client.post("/v1/fetch", json={"url": "https://example.com/x"})

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["cache_hit"] is True
    assert cascade.call_count == 1
