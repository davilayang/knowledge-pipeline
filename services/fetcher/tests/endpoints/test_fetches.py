"""Tests for POST/GET/DELETE /v1/fetches."""

import time
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from fetcher.app import create_app
from fetcher.fetch_service import FetchOutcome


def _setup_envs(monkeypatch, tmp_db_path: str) -> None:
    monkeypatch.setenv("FETCHER_DB_PATH", tmp_db_path)
    monkeypatch.setenv("FETCHER_JINA_API_KEY", "x")
    monkeypatch.setenv("FETCHER_SOCKS5_URL", "socks5://x")
    monkeypatch.setenv("FETCHER_LLAMA_PARSE_API_KEY", "x")


def _outcome() -> FetchOutcome:
    return FetchOutcome(
        kind="success",
        markdown="x",
        source_type="article",
        canonical_url="https://x",
        tier_used="jina",
        fetched_at="2026-06-06T00:00:00Z",
        cache_hit=False,
        etag="abc",
        tier_log=[],
        metadata={},
    )


def test_post_fetches_creates_job(monkeypatch, tmp_db_path: str) -> None:
    _setup_envs(monkeypatch, tmp_db_path)
    app = create_app()

    with patch("fetcher.workers.run_fetch_request", new_callable=AsyncMock) as runner:
        runner.return_value = _outcome()
        with TestClient(app) as client:
            response = client.post(
                "/v1/fetches",
                json={"requests": [{"url": "https://example.com/a"}]},
            )

    assert response.status_code == 202
    assert "job_id" in response.json()["fetches"][0]


def test_post_fetches_partial_validation_failure(monkeypatch, tmp_db_path: str) -> None:
    _setup_envs(monkeypatch, tmp_db_path)
    app = create_app()

    with TestClient(app) as client:
        response = client.post(
            "/v1/fetches",
            json={"requests": [{"url": "https://example.com/ok"}, {"url": "not-a-url"}]},
        )

    body = response.json()
    assert response.status_code == 202
    assert "job_id" in body["fetches"][0]
    assert body["fetches"][1]["error"]["code"] == "BAD_URL"


def test_post_fetches_rejects_batch_over_max(monkeypatch, tmp_db_path: str) -> None:
    _setup_envs(monkeypatch, tmp_db_path)
    monkeypatch.setenv("FETCHER_BATCH_MAX", "1")
    app = create_app()

    with TestClient(app) as client:
        response = client.post(
            "/v1/fetches",
            json={
                "requests": [
                    {"url": "https://example.com/a"},
                    {"url": "https://example.com/b"},
                ]
            },
        )

    assert response.status_code == 400
    assert response.json()["code"] == "BATCH_TOO_LARGE"


def test_get_fetches_returns_done_job(monkeypatch, tmp_db_path: str) -> None:
    _setup_envs(monkeypatch, tmp_db_path)
    app = create_app()

    with patch("fetcher.workers.run_fetch_request", new_callable=AsyncMock) as runner:
        runner.return_value = _outcome()
        with TestClient(app) as client:
            response = client.post(
                "/v1/fetches",
                json={"requests": [{"url": "https://example.com/a"}]},
            )
            job_id = response.json()["fetches"][0]["job_id"]
            for _ in range(50):
                poll = client.get(f"/v1/fetches/{job_id}")
                if poll.json()["status"] == "done":
                    break
                time.sleep(0.02)

    assert poll.status_code == 200
    res_body = poll.json()
    if "result" not in res_body:
        print(f"DEBUG: job_id={job_id} body={res_body}")
    assert "result" in res_body
    assert res_body["result"]["markdown"] == "x"
