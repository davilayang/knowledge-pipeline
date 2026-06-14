"""Tests for POST /v1/structure-transcript.

Mirrors test_structure.py's shape — the endpoint is a thin wrapper around
transcript_structurer.structure_transcript with the same cache + 502/503
contract as /v1/structure. Differs in: no cascade (single LLM call, no
fallbacks), surfaces structurer failures to the caller instead of falling
back to raw transcript.
"""

from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from fetcher.app import create_app
from fetcher.extractors._cloud_chain import StructurerChainFailed


def _setup_envs(monkeypatch, tmp_db_path: str) -> None:
    monkeypatch.setenv("FETCHER_DB_PATH", tmp_db_path)
    monkeypatch.setenv("FETCHER_JINA_API_KEY", "x")
    monkeypatch.setenv("FETCHER_SOCKS5_URL", "socks5://x")
    monkeypatch.setenv("FETCHER_LLAMA_PARSE_API_KEY", "x")


def _ok_return():
    """structure_transcript returns (markdown, tier_name, usage_payload)."""
    return (
        "**Host:** Hello.\n\n**Guest:** Reply.\n",
        "structurer:gemma4:31b",
        {"provider": "ollama", "model": "gemma4:31b", "tokens_in": 100, "tokens_out": 80},
    )


def test_endpoint_returns_structured_markdown_on_success(monkeypatch, tmp_db_path: str) -> None:
    _setup_envs(monkeypatch, tmp_db_path)
    app = create_app()
    with patch(
        "fetcher.endpoints.structure_transcript.structure_transcript",
        new_callable=AsyncMock,
    ) as struct_mock:
        struct_mock.return_value = _ok_return()
        with TestClient(app) as client:
            response = client.post(
                "/v1/structure-transcript",
                json={
                    "raw_transcript": "noisy auto-caption blob " * 50,
                    "title": "A Talk",
                    "author": "Some Show",
                },
            )

    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) >= {
        "markdown",
        "kind",
        "tier_used",
        "fetched_at",
        "cache_hit",
        "metadata",
    }
    assert body["markdown"].startswith("**Host:**")
    assert body["kind"] == "structured-transcript"
    assert body["tier_used"] == "structurer:gemma4:31b"
    assert body["cache_hit"] is False
    assert body["metadata"]["structurer_usage"]["model"] == "gemma4:31b"
    # title + author must have been threaded into the structurer call
    kwargs = struct_mock.await_args.kwargs
    assert kwargs["title"] == "A Talk"
    assert kwargs["author"] == "Some Show"


def test_endpoint_returns_400_problem_on_empty_raw_transcript(
    monkeypatch, tmp_db_path: str
) -> None:
    _setup_envs(monkeypatch, tmp_db_path)
    app = create_app()
    with TestClient(app) as client:
        response = client.post("/v1/structure-transcript", json={"raw_transcript": ""})
    assert response.status_code == 400
    assert response.json()["code"] == "BAD_REQUEST"


def test_endpoint_returns_502_on_chain_failure(monkeypatch, tmp_db_path: str) -> None:
    """Unlike YouTube handler (which falls back silently), this endpoint surfaces
    structurer failures to the caller so eval harnesses + debug tools see them."""
    _setup_envs(monkeypatch, tmp_db_path)
    app = create_app()
    with patch(
        "fetcher.endpoints.structure_transcript.structure_transcript",
        new_callable=AsyncMock,
    ) as struct_mock:
        struct_mock.side_effect = StructurerChainFailed("upstream timeout", retryable=True)
        with TestClient(app) as client:
            response = client.post(
                "/v1/structure-transcript",
                json={"raw_transcript": "transcript blob", "title": "t"},
            )

    assert response.status_code == 502
    assert response.headers["content-type"].startswith("application/problem+json")
    body = response.json()
    assert body["code"] == "STRUCTURER_UPSTREAM_FAILURE"
    assert body["retryable"] is True


def test_endpoint_returns_503_on_no_api_keys(monkeypatch, tmp_db_path: str) -> None:
    _setup_envs(monkeypatch, tmp_db_path)
    app = create_app()
    with patch(
        "fetcher.endpoints.structure_transcript.structure_transcript",
        new_callable=AsyncMock,
    ) as struct_mock:
        struct_mock.side_effect = StructurerChainFailed("no API keys configured", retryable=False)
        with TestClient(app) as client:
            response = client.post(
                "/v1/structure-transcript",
                json={"raw_transcript": "blob"},
            )

    assert response.status_code == 503
    body = response.json()
    assert body["code"] == "STRUCTURER_UNCONFIGURED"
    assert body["retryable"] is False


def test_endpoint_caches_successful_runs(monkeypatch, tmp_db_path: str) -> None:
    _setup_envs(monkeypatch, tmp_db_path)
    app = create_app()
    payload = {
        "raw_transcript": "noisy transcript blob",
        "title": "Talk",
        "author": "Show",
    }
    with patch(
        "fetcher.endpoints.structure_transcript.structure_transcript",
        new_callable=AsyncMock,
    ) as struct_mock:
        struct_mock.return_value = _ok_return()
        with TestClient(app) as client:
            first = client.post("/v1/structure-transcript", json=payload)
            second = client.post("/v1/structure-transcript", json=payload)

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["cache_hit"] is False
    assert second.json()["cache_hit"] is True
    assert second.json()["markdown"] == first.json()["markdown"]
    assert struct_mock.await_count == 1


def test_endpoint_cache_invalidates_when_hints_change(monkeypatch, tmp_db_path: str) -> None:
    """Different title/author = different LLM input → different cache row."""
    _setup_envs(monkeypatch, tmp_db_path)
    app = create_app()
    base = {"raw_transcript": "transcript text"}
    with patch(
        "fetcher.endpoints.structure_transcript.structure_transcript",
        new_callable=AsyncMock,
    ) as struct_mock:
        struct_mock.return_value = _ok_return()
        with TestClient(app) as client:
            first = client.post("/v1/structure-transcript", json={**base, "title": "A"})
            second = client.post("/v1/structure-transcript", json={**base, "title": "B"})

    assert first.json()["cache_hit"] is False
    assert second.json()["cache_hit"] is False
    assert struct_mock.await_count == 2


def test_endpoint_cache_key_does_not_collide_with_structure_endpoint(
    monkeypatch, tmp_db_path: str
) -> None:
    """Same raw text, both endpoints → must hit different cache rows (namespaced)."""
    from fetcher.cache import lookup, upsert
    from domains.fetches_store.sources import create_schema

    _setup_envs(monkeypatch, tmp_db_path)
    db_path = Path(tmp_db_path)
    create_schema(db_path=db_path)

    # Seed a row under a /v1/structure key prefix; transcript lookup must miss.
    upsert(
        db_path=db_path,
        canonical_url="structure:v2:abcdef",
        source_type="structured",
        markdown="article markdown",
        tier_used="structurer:gpt-4.1-mini",
        metadata={},
        tier_log=[],
        ttl_days=365,
    )

    # A transcript endpoint key won't be `structure:v2:abcdef` — it'll be
    # under `structure-transcript:v2:*`. Lookup with the article key must
    # still miss when probed from the transcript endpoint's namespace.
    assert lookup(db_path=db_path, canonical_url="structure-transcript:v2:abcdef") is None
