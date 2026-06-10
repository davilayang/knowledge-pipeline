"""Tests for POST /v1/structure."""

from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from fetcher.app import create_app
from fetcher.canonicalize import CanonicalResult
from fetcher.extractors.structure import StructurerCascadeFailed
from fetcher.types import FetchResult, TierLogEntry


def _setup_envs(monkeypatch, tmp_db_path: str) -> None:
    monkeypatch.setenv("FETCHER_DB_PATH", tmp_db_path)
    monkeypatch.setenv("FETCHER_JINA_API_KEY", "x")
    monkeypatch.setenv("FETCHER_SOCKS5_URL", "socks5://x")
    monkeypatch.setenv("FETCHER_LLAMA_PARSE_API_KEY", "x")


def _ok_result() -> FetchResult:
    return FetchResult(
        markdown="# Title\n\nBody",
        kind="structured",
        canonical_url="https://example.com/a",
        tier_used="structurer:test-model",
        fetched_at="2026-06-10T00:00:00Z",
        cache_hit=False,
        etag="",
        tier_log=[
            TierLogEntry(tier="trafilatura", status=None, chars=0, error="empty", validated=False),
            TierLogEntry(tier="passthrough", status=None, chars=0, error="rejected", validated=False),
            TierLogEntry(
                tier="structurer:test-model", status=None, chars=14, error=None, validated=True
            ),
        ],
        metadata={"model": "test-model", "prompt_version": "v1"},
    )


def test_structure_endpoint_returns_fetchresult_wire_shape(monkeypatch, tmp_db_path: str) -> None:
    _setup_envs(monkeypatch, tmp_db_path)
    app = create_app()
    with (
        patch("fetcher.endpoints.structure.canonicalize") as can_mock,
        patch(
            "fetcher.endpoints.structure.run_cascade", new_callable=AsyncMock
        ) as cascade,
    ):
        can_mock.return_value = CanonicalResult(
            "https://example.com/a", "https://example.com/a", [], []
        )
        cascade.return_value = _ok_result()
        with TestClient(app) as client:
            response = client.post(
                "/v1/structure",
                json={"raw_content": "noisy paste", "source_url": "https://example.com/a"},
            )

    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) == {
        "markdown",
        "kind",
        "canonical_url",
        "tier_used",
        "fetched_at",
        "cache_hit",
        "etag",
        "tier_log",
        "metadata",
    }
    assert body["markdown"] == "# Title\n\nBody"
    assert body["kind"] == "structured"
    assert body["tier_used"] == "structurer:test-model"
    assert body["cache_hit"] is False
    assert body["metadata"]["prompt_version"] == "v1"
    assert len(body["tier_log"]) == 3


def test_structure_endpoint_threads_hint_kwargs_into_cascade(monkeypatch, tmp_db_path: str) -> None:
    _setup_envs(monkeypatch, tmp_db_path)
    app = create_app()
    with (
        patch("fetcher.endpoints.structure.canonicalize") as can_mock,
        patch(
            "fetcher.endpoints.structure.run_cascade", new_callable=AsyncMock
        ) as cascade,
    ):
        can_mock.return_value = CanonicalResult(
            "https://example.com/a", "https://example.com/a", [], []
        )
        cascade.return_value = _ok_result()
        with TestClient(app) as client:
            client.post(
                "/v1/structure",
                json={
                    "raw_content": "paste",
                    "source_url": "https://example.com/a",
                    "title": "Real Title",
                    "content_date": "2026-06-01",
                    "author_name": "Jane Doe",
                },
            )

    kwargs = cascade.await_args.kwargs
    assert kwargs["title"] == "Real Title"
    assert kwargs["content_date"] == "2026-06-01"
    assert kwargs["author_name"] == "Jane Doe"
    assert kwargs["source_url"] == "https://example.com/a"
    assert kwargs["raw_content"] == "paste"


def test_structure_endpoint_canonicalizes_source_url(monkeypatch, tmp_db_path: str) -> None:
    _setup_envs(monkeypatch, tmp_db_path)
    app = create_app()
    with (
        patch("fetcher.endpoints.structure.canonicalize") as can_mock,
        patch(
            "fetcher.endpoints.structure.run_cascade", new_callable=AsyncMock
        ) as cascade,
    ):
        can_mock.return_value = CanonicalResult(
            "https://example.com/a?utm_source=x",
            "https://example.com/a",
            [],
            ["utm_source"],
        )
        cascade.return_value = _ok_result()
        with TestClient(app) as client:
            client.post(
                "/v1/structure",
                json={
                    "raw_content": "paste",
                    "source_url": "https://example.com/a?utm_source=x",
                },
            )

    can_mock.assert_called_once_with("https://example.com/a?utm_source=x")
    assert cascade.await_args.kwargs["source_url"] == "https://example.com/a"


def test_structure_endpoint_handles_missing_source_url(monkeypatch, tmp_db_path: str) -> None:
    _setup_envs(monkeypatch, tmp_db_path)
    app = create_app()
    result = _ok_result()
    result.canonical_url = ""
    with (
        patch("fetcher.endpoints.structure.canonicalize") as can_mock,
        patch(
            "fetcher.endpoints.structure.run_cascade", new_callable=AsyncMock
        ) as cascade,
    ):
        cascade.return_value = result
        with TestClient(app) as client:
            response = client.post("/v1/structure", json={"raw_content": "paste"})

    assert response.status_code == 200
    can_mock.assert_not_called()
    assert cascade.await_args.kwargs["source_url"] == ""
    assert response.json()["canonical_url"] == ""


def test_structure_endpoint_returns_502_problem_when_cascade_exhausts_with_transient(
    monkeypatch, tmp_db_path: str
) -> None:
    _setup_envs(monkeypatch, tmp_db_path)
    app = create_app()
    tier_log = [
        TierLogEntry(tier="trafilatura", status=None, chars=0, error="empty", validated=False),
        TierLogEntry(tier="passthrough", status=None, chars=0, error="rejected", validated=False),
        TierLogEntry(tier="structurer", status=None, chars=0, error="upstream timeout", validated=False),
    ]
    with (
        patch("fetcher.endpoints.structure.canonicalize") as can_mock,
        patch(
            "fetcher.endpoints.structure.run_cascade", new_callable=AsyncMock
        ) as cascade,
    ):
        can_mock.return_value = CanonicalResult(
            "https://example.com/a", "https://example.com/a", [], []
        )
        cascade.side_effect = StructurerCascadeFailed(
            "cascade exhausted",
            retryable=True,
            tier_log=tier_log,
            last_error="upstream timeout",
        )
        with TestClient(app) as client:
            response = client.post(
                "/v1/structure",
                json={"raw_content": "paste", "source_url": "https://example.com/a"},
            )

    assert response.status_code == 502
    assert response.headers["content-type"].startswith("application/problem+json")
    body = response.json()
    assert body["code"] == "STRUCTURER_UPSTREAM_FAILURE"
    assert body["retryable"] is True
    assert len(body["tier_log"]) == 3


def test_structure_endpoint_returns_503_problem_when_no_api_keys_configured(
    monkeypatch, tmp_db_path: str
) -> None:
    _setup_envs(monkeypatch, tmp_db_path)
    app = create_app()
    with (
        patch("fetcher.endpoints.structure.canonicalize") as can_mock,
        patch(
            "fetcher.endpoints.structure.run_cascade", new_callable=AsyncMock
        ) as cascade,
    ):
        can_mock.return_value = CanonicalResult(
            "https://example.com/a", "https://example.com/a", [], []
        )
        cascade.side_effect = StructurerCascadeFailed(
            "no API keys",
            retryable=False,
            tier_log=[],
            last_error="no API keys configured",
        )
        with TestClient(app) as client:
            response = client.post(
                "/v1/structure",
                json={"raw_content": "paste", "source_url": "https://example.com/a"},
            )

    assert response.status_code == 503
    body = response.json()
    assert body["code"] == "STRUCTURER_UNCONFIGURED"
    assert body["retryable"] is False


def test_structure_endpoint_returns_400_problem_on_empty_raw_content(
    monkeypatch, tmp_db_path: str
) -> None:
    _setup_envs(monkeypatch, tmp_db_path)
    app = create_app()
    with TestClient(app) as client:
        response = client.post("/v1/structure", json={"raw_content": ""})
    assert response.status_code == 400
    assert response.json()["code"] == "BAD_REQUEST"
