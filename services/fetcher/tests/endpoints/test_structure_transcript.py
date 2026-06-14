"""Tests for POST /v1/structure-transcript.

Mirrors test_structure.py's shape — the endpoint is a thin wrapper around
transcript_structurer.structure_transcript with the same cache + 502/503
contract as /v1/structure. Differs in: no cascade (single LLM call, no
fallbacks), surfaces structurer failures to the caller instead of falling
back to raw transcript.
"""

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


def test_structure_and_structure_transcript_use_separate_cache_namespaces(
    monkeypatch, tmp_db_path: str
) -> None:
    """Real namespacing check: POST to both endpoints with identical text;
    each must produce its own cache row. A naive `content_sha`-only key
    would collide and silently return structured-article markdown to a
    structured-transcript caller (or vice-versa)."""
    from unittest.mock import AsyncMock, patch

    from fetcher.canonicalize import CanonicalResult

    _setup_envs(monkeypatch, tmp_db_path)
    app = create_app()

    article_md = "# Article body\n\nstructured article output"
    transcript_md = "**Host:** transcript output"
    shared_text = "the same input text " * 30

    article_result = type(
        "FetchResult",
        (),
        {
            "markdown": article_md,
            "kind": "structured",
            "canonical_url": "https://x/a",
            "tier_used": "structurer:gpt-4.1-mini",
            "fetched_at": "2026-06-14T00:00:00Z",
            "cache_hit": False,
            "etag": "",
            "tier_log": [],
            "metadata": {"model": "gpt-4.1-mini"},
        },
    )()

    with (
        patch("fetcher.endpoints.structure.canonicalize") as can_mock,
        patch("fetcher.endpoints.structure.run_cascade", new_callable=AsyncMock) as cascade,
        patch(
            "fetcher.endpoints.structure_transcript.structure_transcript",
            new_callable=AsyncMock,
        ) as struct_mock,
    ):
        can_mock.return_value = CanonicalResult("https://x/a", "https://x/a", [], [])
        cascade.return_value = article_result
        struct_mock.return_value = (transcript_md, "structurer:gemma4:31b", {})

        with TestClient(app) as client:
            article_resp = client.post(
                "/v1/structure",
                json={"raw_content": shared_text, "source_url": "https://x/a"},
            )
            transcript_resp = client.post(
                "/v1/structure-transcript",
                json={"raw_transcript": shared_text},
            )

    assert article_resp.json()["markdown"] == article_md
    assert transcript_resp.json()["markdown"] == transcript_md
    # Both were fresh writes (cache miss on both) — proves they didn't
    # collide on the same cache row. A naive shared key would have made
    # transcript_resp a cache_hit returning article_md.
    assert article_resp.json()["cache_hit"] is False
    assert transcript_resp.json()["cache_hit"] is False
    assert article_resp.json()["markdown"] != transcript_resp.json()["markdown"]
