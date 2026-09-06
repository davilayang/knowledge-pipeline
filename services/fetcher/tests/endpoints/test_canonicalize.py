"""Tests for GET /v1/canonicalize."""

from unittest.mock import patch

from fastapi.testclient import TestClient

from fetcher.app import create_app
from fetcher.canonicalize import CanonicalResult


def _setup_envs(monkeypatch, tmp_db_path: str) -> None:
    monkeypatch.setenv("FETCHER_DB_PATH", tmp_db_path)
    monkeypatch.setenv("FETCHER_JINA_API_KEY", "x")
    monkeypatch.setenv("FETCHER_SOCKS5_URL", "socks5://x")
    monkeypatch.setenv("FETCHER_LLAMA_PARSE_API_KEY", "x")


def test_canonicalize_returns_canonical_url(monkeypatch, tmp_db_path: str) -> None:
    _setup_envs(monkeypatch, tmp_db_path)
    app = create_app()

    with patch("fetcher.cache.canonicalize") as can_mock:
        can_mock.return_value = CanonicalResult(
            input_url="https://t.co/abc",
            canonical_url="https://medium.com/the-article",
            redirects_followed=["https://t.co/abc", "https://medium.com/the-article"],
            params_stripped=["utm_source"],
        )
        with TestClient(app) as client:
            response = client.get("/v1/canonicalize", params={"url": "https://t.co/abc"})

    assert response.status_code == 200
    body = response.json()
    # input_url pass-through is covered by the canonicalize unit test
    # (test_canonicalize.py::test_input_url_preserved_in_result).
    assert body["canonical_url"] == "https://medium.com/the-article"
    assert body["cache_hit"] is False


def test_canonicalize_cache_hit_on_second_call(monkeypatch, tmp_db_path: str) -> None:
    _setup_envs(monkeypatch, tmp_db_path)
    app = create_app()

    with patch("fetcher.cache.canonicalize") as can_mock:
        can_mock.return_value = CanonicalResult(
            input_url="https://t.co/abc",
            canonical_url="https://example.com/x",
            redirects_followed=[],
            params_stripped=[],
        )
        with TestClient(app) as client:
            first = client.get("/v1/canonicalize", params={"url": "https://t.co/abc"})
            second = client.get("/v1/canonicalize", params={"url": "https://t.co/abc"})

    assert first.json()["cache_hit"] is False
    assert second.json()["cache_hit"] is True
    assert can_mock.call_count == 1


def test_canonicalize_force_refresh_bypasses_cache(monkeypatch, tmp_db_path: str) -> None:
    _setup_envs(monkeypatch, tmp_db_path)
    app = create_app()

    with patch("fetcher.cache.canonicalize") as can_mock:
        can_mock.return_value = CanonicalResult(
            input_url="https://t.co/abc",
            canonical_url="https://example.com/x",
            redirects_followed=[],
            params_stripped=[],
        )
        with TestClient(app) as client:
            client.get("/v1/canonicalize", params={"url": "https://t.co/abc"})
            second = client.get(
                "/v1/canonicalize",
                params={"url": "https://t.co/abc", "force_refresh": "true"},
            )

    # Observable: force_refresh response carries cache_hit=False
    # (instead of the True it would have without the flag).
    assert second.json()["cache_hit"] is False
    assert can_mock.call_count == 2
