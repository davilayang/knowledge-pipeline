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
        patch("fetcher.endpoints.structure.run_cascade", new_callable=AsyncMock) as cascade,
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
    assert len(body["tier_log"]) == 2


def test_structure_endpoint_threads_hint_kwargs_into_cascade(monkeypatch, tmp_db_path: str) -> None:
    _setup_envs(monkeypatch, tmp_db_path)
    app = create_app()
    with (
        patch("fetcher.endpoints.structure.canonicalize") as can_mock,
        patch("fetcher.endpoints.structure.run_cascade", new_callable=AsyncMock) as cascade,
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

    # raw_content + source_url are the load-bearing kwargs: raw_content
    # because it's the input under structuring, source_url because the
    # canonicalization step happens upstream and a bug there would surface
    # only via this assertion. title/author/content_date forwarding is
    # plumbing and is already covered at the structurer module level by
    # test_cloud_chain.py::test_build_user_message_*.
    kwargs = cascade.await_args.kwargs
    assert kwargs["raw_content"] == "paste"
    assert kwargs["source_url"] == "https://example.com/a"


def test_structure_endpoint_canonicalizes_source_url(monkeypatch, tmp_db_path: str) -> None:
    _setup_envs(monkeypatch, tmp_db_path)
    app = create_app()
    with (
        patch("fetcher.endpoints.structure.canonicalize") as can_mock,
        patch("fetcher.endpoints.structure.run_cascade", new_callable=AsyncMock) as cascade,
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
        patch("fetcher.endpoints.structure.run_cascade", new_callable=AsyncMock) as cascade,
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
        TierLogEntry(
            tier="structurer", status=None, chars=0, error="upstream timeout", validated=False
        ),
    ]
    with (
        patch("fetcher.endpoints.structure.canonicalize") as can_mock,
        patch("fetcher.endpoints.structure.run_cascade", new_callable=AsyncMock) as cascade,
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
    assert len(body["tier_log"]) == 2


def test_structure_endpoint_returns_503_problem_when_no_api_keys_configured(
    monkeypatch, tmp_db_path: str
) -> None:
    _setup_envs(monkeypatch, tmp_db_path)
    app = create_app()
    with (
        patch("fetcher.endpoints.structure.canonicalize") as can_mock,
        patch("fetcher.endpoints.structure.run_cascade", new_callable=AsyncMock) as cascade,
    ):
        can_mock.return_value = CanonicalResult(
            "https://example.com/a", "https://example.com/a", [], []
        )
        cascade.side_effect = StructurerCascadeFailed(
            "cascade exhausted",
            retryable=False,
            tier_log=[],
            last_error="no API keys configured for any chain provider",
            not_configured=True,
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


def test_structure_endpoint_caches_successful_cloud_runs_only(
    monkeypatch, tmp_db_path: str
) -> None:
    _setup_envs(monkeypatch, tmp_db_path)
    app = create_app()
    with (
        patch("fetcher.endpoints.structure.canonicalize") as can_mock,
        patch("fetcher.endpoints.structure.run_cascade", new_callable=AsyncMock) as cascade,
    ):
        can_mock.return_value = CanonicalResult(
            "https://example.com/a", "https://example.com/a", [], []
        )
        cascade.return_value = _ok_result()
        with TestClient(app) as client:
            first = client.post(
                "/v1/structure",
                json={"raw_content": "noisy paste", "source_url": "https://example.com/a"},
            )
            second = client.post(
                "/v1/structure",
                json={"raw_content": "noisy paste", "source_url": "https://example.com/a"},
            )

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["cache_hit"] is False
    assert second.json()["cache_hit"] is True
    assert second.json()["markdown"] == first.json()["markdown"]
    assert second.json()["tier_used"] == "structurer:test-model"
    assert cascade.await_count == 1


def _trafilatura_result() -> FetchResult:
    return FetchResult(
        markdown="# T\n\nbody",
        kind="structured",
        canonical_url="https://example.com/a",
        tier_used="trafilatura",
        fetched_at="2026-06-10T00:00:00Z",
        cache_hit=False,
        etag="",
        tier_log=[
            TierLogEntry(tier="trafilatura", status=None, chars=8, error=None, validated=True),
        ],
        metadata={},
    )


def test_structure_endpoint_does_not_cache_trafilatura(monkeypatch, tmp_db_path: str) -> None:
    _setup_envs(monkeypatch, tmp_db_path)
    app = create_app()
    with (
        patch("fetcher.endpoints.structure.canonicalize") as can_mock,
        patch("fetcher.endpoints.structure.run_cascade", new_callable=AsyncMock) as cascade,
    ):
        can_mock.return_value = CanonicalResult(
            "https://example.com/a", "https://example.com/a", [], []
        )
        cascade.return_value = _trafilatura_result()
        with TestClient(app) as client:
            first = client.post(
                "/v1/structure",
                json={"raw_content": "<html>...</html>", "source_url": "https://example.com/a"},
            )
            second = client.post(
                "/v1/structure",
                json={"raw_content": "<html>...</html>", "source_url": "https://example.com/a"},
            )

    assert first.json()["cache_hit"] is False
    assert second.json()["cache_hit"] is False
    assert cascade.await_count == 2


def test_structure_endpoint_does_not_collide_with_fetch_cache(
    monkeypatch, tmp_db_path: str
) -> None:
    """Real cross-endpoint isolation: a /v1/fetch row at a canonical URL must
    NOT satisfy a subsequent /v1/structure call for the same source_url. The
    old test seeded a made-up structurer key — proving two literal strings
    differ, not that the endpoints actually produce non-colliding keys."""
    _setup_envs(monkeypatch, tmp_db_path)
    app = create_app()

    with (
        patch("fetcher.endpoints.structure.canonicalize") as can_mock,
        patch("fetcher.endpoints.structure.run_cascade", new_callable=AsyncMock) as cascade,
    ):
        can_mock.return_value = CanonicalResult(
            "https://example.com/a", "https://example.com/a", [], []
        )
        cascade.return_value = _ok_result()

        with TestClient(app) as client:
            # Seed a /v1/fetch-shaped cache row at the canonical URL by
            # writing directly through the cache layer (simulates a prior
            # /v1/fetch hit for this URL).
            from pathlib import Path

            from domains.fetch_store.sources import create_schema
            from fetcher.cache import upsert as cache_upsert

            db_path = Path(tmp_db_path)
            create_schema(db_path=db_path)
            cache_upsert(
                db_path=db_path,
                canonical_url="https://example.com/a",
                source_type="article",
                markdown="from /v1/fetch — not what /v1/structure produces",
                tier_used="jina",
                metadata={},
                tier_log=[],
                ttl_days=365,
            )

            # /v1/structure on the same source URL must NOT pick up the
            # fetch row — it must run the cascade and return its own markdown.
            response = client.post(
                "/v1/structure",
                json={"raw_content": "paste", "source_url": "https://example.com/a"},
            )

    assert response.status_code == 200
    body = response.json()
    assert body["markdown"] != "from /v1/fetch — not what /v1/structure produces"
    assert body["cache_hit"] is False


def test_structure_endpoint_cache_invalidates_when_prompt_changes(
    monkeypatch, tmp_db_path: str
) -> None:
    """Regression for the latent /v1/structure cache bug PR 1 (Phase A) fixes.

    Editing the active structurer prompt must produce a cache miss on the next request
    with otherwise-identical inputs. Today's key omits prompt content, so the bug
    surfaces as a silent cache hit returning markdown structured under the OLD
    prompt — invisible to operators editing prompts.
    """
    _setup_envs(monkeypatch, tmp_db_path)
    app = create_app()

    with (
        patch("fetcher.endpoints.structure.canonicalize") as can_mock,
        patch("fetcher.endpoints.structure.run_cascade", new_callable=AsyncMock) as cascade,
        patch("fetcher.endpoints.structure._structure_extractor.get_prompt") as get_prompt,
    ):
        can_mock.return_value = CanonicalResult(
            "https://example.com/a", "https://example.com/a", [], []
        )
        cascade.return_value = _ok_result()

        get_prompt.return_value = "prompt v1 contents"
        with TestClient(app) as client:
            first = client.post(
                "/v1/structure",
                json={"raw_content": "paste", "source_url": "https://example.com/a"},
            )

        get_prompt.return_value = "prompt v2 contents — edited!"
        with TestClient(app) as client:
            second = client.post(
                "/v1/structure",
                json={"raw_content": "paste", "source_url": "https://example.com/a"},
            )

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["cache_hit"] is False
    # The bug: today this is True (stale cache hit). After PR 1: False (proper miss).
    assert second.json()["cache_hit"] is False
    assert cascade.await_count == 2


def test_structure_endpoint_cache_invalidates_when_chain_config_changes(
    monkeypatch, tmp_db_path: str
) -> None:
    """Reordering / swapping chain entries must also invalidate cache.

    Today's key only includes chain HEAD provider+model; downstream entries
    are invisible. The cache_key_components helper closes that gap by sha-ing
    the entire chain config (provider/model/base_url/attempt_timeout per entry).
    """
    from fetcher.extractors._cloud_chain import ChainEntry

    _setup_envs(monkeypatch, tmp_db_path)
    app = create_app()

    chain_v1 = [ChainEntry(model="gpt-4.1-mini", provider="openai", attempt_timeout=30.0)]
    chain_v2 = [ChainEntry(model="gpt-4.1-mini", provider="openai", attempt_timeout=60.0)]

    with (
        patch("fetcher.endpoints.structure.canonicalize") as can_mock,
        patch("fetcher.endpoints.structure.run_cascade", new_callable=AsyncMock) as cascade,
        patch("fetcher.endpoints.structure._structure_extractor.get_chain") as get_chain,
    ):
        can_mock.return_value = CanonicalResult(
            "https://example.com/a", "https://example.com/a", [], []
        )
        cascade.return_value = _ok_result()

        get_chain.return_value = chain_v1
        with TestClient(app) as client:
            first = client.post(
                "/v1/structure",
                json={"raw_content": "paste", "source_url": "https://example.com/a"},
            )

        get_chain.return_value = chain_v2
        with TestClient(app) as client:
            second = client.post(
                "/v1/structure",
                json={"raw_content": "paste", "source_url": "https://example.com/a"},
            )

    assert first.json()["cache_hit"] is False
    assert second.json()["cache_hit"] is False
    assert cascade.await_count == 2
