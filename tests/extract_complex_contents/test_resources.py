"""Tests for extract_complex_contents resources.

Mocks the external SDKs (notion-client, openai, requests, curl-cffi) at
the import location in their respective modules. Covers what the asset bodies
depend on:
- Notion query/get/update payload shapes
- Fetcher dispatch (YouTube, arXiv, article cascade)
- ExtractorRegistry builds the three-call extractor + delegates correctly
- Store thin delegation
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
from orchestrators.defs.extract_complex_contents.resources import (
    ExtractorRegistry,
    FetcherResource,
)
from orchestrators.defs.shared.queue_resources import NotionQueueResource, QueueStoreResource

# -------- NotionQueueResource --------


def _make_notion_with_mocked_client() -> tuple[NotionQueueResource, MagicMock]:
    resource = NotionQueueResource(
        integration_token="secret_x",
        queue_db_id="db-123",
        queue_data_source_id="ds-456",
    )
    mock_client = MagicMock()
    with patch.object(NotionQueueResource, "_client", return_value=mock_client):
        pass
    # Re-patch for actual use — caller wraps with patch.object too.
    return resource, mock_client


def test_notion_query_for_extract_filters_by_fetching_and_content_type():
    resource = NotionQueueResource(
        integration_token="secret_x",
        queue_db_id="db-123",
        queue_data_source_id="ds-456",
    )
    fake_client = MagicMock()
    fake_client.data_sources.query.return_value = {"results": [{"id": "p-1"}]}
    with patch.object(NotionQueueResource, "_client", return_value=fake_client):
        rows = resource.query_for_extract(page_size=2, supported_content_types=("YouTube", "arXiv"))
    fake_client.data_sources.query.assert_called_once_with(
        data_source_id="ds-456",
        filter={
            "and": [
                {"property": "Status", "status": {"equals": "Fetching"}},
                {
                    "or": [
                        {"property": "Content Type", "select": {"equals": "YouTube"}},
                        {"property": "Content Type", "select": {"equals": "arXiv"}},
                    ]
                },
            ]
        },
        page_size=2,
    )
    assert rows == [{"id": "p-1"}]


def test_notion_query_for_extract_single_type_uses_flat_clause():
    resource = NotionQueueResource(
        integration_token="secret_x",
        queue_db_id="db-123",
        queue_data_source_id="ds-456",
    )
    fake_client = MagicMock()
    fake_client.data_sources.query.return_value = {"results": []}
    with patch.object(NotionQueueResource, "_client", return_value=fake_client):
        resource.query_for_extract(page_size=1, supported_content_types=("YouTube",))
    call_filter = fake_client.data_sources.query.call_args.kwargs["filter"]
    type_clause = call_filter["and"][1]
    assert type_clause == {"property": "Content Type", "select": {"equals": "YouTube"}}


def test_notion_update_status_writes_native_status_property():
    resource = NotionQueueResource(
        integration_token="secret_x",
        queue_db_id="db-123",
        queue_data_source_id="ds-456",
    )
    fake_client = MagicMock()
    with patch.object(NotionQueueResource, "_client", return_value=fake_client):
        resource.update_status("page-id", "Ready")
    fake_client.pages.update.assert_called_once_with(
        page_id="page-id",
        properties={"Status": {"status": {"name": "Ready"}}},
    )


def test_notion_update_status_writes_description_when_provided():
    resource = NotionQueueResource(
        integration_token="secret_x",
        queue_db_id="db-123",
        queue_data_source_id="ds-456",
    )
    fake_client = MagicMock()
    with patch.object(NotionQueueResource, "_client", return_value=fake_client):
        resource.update_status("page-id", "Ready", description="Sharper blurb.")
    props = fake_client.pages.update.call_args.kwargs["properties"]
    assert props["Status"] == {"status": {"name": "Ready"}}
    assert props["Description"]["rich_text"][0]["text"]["content"] == "Sharper blurb."


def test_notion_update_status_strips_description_and_skips_when_empty():
    resource = NotionQueueResource(
        integration_token="secret_x",
        queue_db_id="db-123",
        queue_data_source_id="ds-456",
    )
    fake_client = MagicMock()
    with patch.object(NotionQueueResource, "_client", return_value=fake_client):
        resource.update_status("page-id", "Ready", description="\n  \n")
    props = fake_client.pages.update.call_args.kwargs["properties"]
    assert "Description" not in props


def test_notion_update_status_omits_description_when_not_provided():
    resource = NotionQueueResource(
        integration_token="secret_x",
        queue_db_id="db-123",
        queue_data_source_id="ds-456",
    )
    fake_client = MagicMock()
    with patch.object(NotionQueueResource, "_client", return_value=fake_client):
        resource.update_status("page-id", "Ready")
    props = fake_client.pages.update.call_args.kwargs["properties"]
    assert "Description" not in props


def test_notion_update_status_writes_name_when_provided():
    resource = NotionQueueResource(
        integration_token="secret_x",
        queue_db_id="db-123",
        queue_data_source_id="ds-456",
    )
    fake_client = MagicMock()
    with patch.object(NotionQueueResource, "_client", return_value=fake_client):
        resource.update_status("page-id", "Ready", name="Sharper Title from Extractor")
    props = fake_client.pages.update.call_args.kwargs["properties"]
    assert props["Status"] == {"status": {"name": "Ready"}}
    assert props["Name"]["title"][0]["text"]["content"] == "Sharper Title from Extractor"


def test_notion_update_status_strips_name_and_skips_when_empty():
    resource = NotionQueueResource(
        integration_token="secret_x",
        queue_db_id="db-123",
        queue_data_source_id="ds-456",
    )
    fake_client = MagicMock()
    with patch.object(NotionQueueResource, "_client", return_value=fake_client):
        resource.update_status("page-id", "Ready", name="   \n  ")
    props = fake_client.pages.update.call_args.kwargs["properties"]
    assert "Name" not in props


def test_notion_update_status_omits_name_when_not_provided():
    resource = NotionQueueResource(
        integration_token="secret_x",
        queue_db_id="db-123",
        queue_data_source_id="ds-456",
    )
    fake_client = MagicMock()
    with patch.object(NotionQueueResource, "_client", return_value=fake_client):
        resource.update_status("page-id", "Ready", description="d")
    props = fake_client.pages.update.call_args.kwargs["properties"]
    assert "Name" not in props


def test_notion_update_status_failed_writes_status_and_error():
    resource = NotionQueueResource(
        integration_token="secret_x",
        queue_db_id="db-123",
        queue_data_source_id="ds-456",
    )
    fake_client = MagicMock()
    with patch.object(NotionQueueResource, "_client", return_value=fake_client):
        resource.update_status_failed("page-id", "fetch returned 403")
    args, kwargs = fake_client.pages.update.call_args
    assert kwargs["page_id"] == "page-id"
    props = kwargs["properties"]
    assert props["Status"] == {"status": {"name": "Failed"}}
    assert props["Error"]["rich_text"][0]["text"]["content"] == "fetch returned 403"


def test_notion_update_status_failed_truncates_long_errors():
    resource = NotionQueueResource(
        integration_token="secret_x",
        queue_db_id="db-123",
        queue_data_source_id="ds-456",
    )
    fake_client = MagicMock()
    with patch.object(NotionQueueResource, "_client", return_value=fake_client):
        resource.update_status_failed("page-id", "x" * 5000)
    props = fake_client.pages.update.call_args.kwargs["properties"]
    assert len(props["Error"]["rich_text"][0]["text"]["content"]) == 1900


def test_notion_get_status_reads_native_status_name():
    resource = NotionQueueResource(
        integration_token="secret_x",
        queue_db_id="db-123",
        queue_data_source_id="ds-456",
    )
    fake_client = MagicMock()
    fake_client.pages.retrieve.return_value = {
        "properties": {"Status": {"status": {"name": "Ready"}}}
    }
    with patch.object(NotionQueueResource, "_client", return_value=fake_client):
        assert resource.get_status("p-1") == "Ready"


# -------- ExtractorRegistry --------


def _make_registry(
    tmp_path: Path,
    monkeypatch,
    *,
    narrative: str = "NARRATIVE PROMPT",
    topic_card: str = "TOPIC CARD PROMPT",
    followups: str = "FOLLOWUPS PROMPT",
) -> "ExtractorRegistry":
    """Patches the prompts dir + the module-level PROMPT_LABEL_* constants
    so the registry resolves to the synthetic files."""
    fake_dir = tmp_path / "prompts"
    fake_dir.mkdir(exist_ok=True)
    (fake_dir / "n.md").write_text(narrative)
    (fake_dir / "t.md").write_text(topic_card)
    (fake_dir / "f.md").write_text(followups)
    monkeypatch.setattr(
        "orchestrators.defs.extract_complex_contents.resources._PROMPTS_DIR",
        fake_dir,
    )
    monkeypatch.setattr(
        "orchestrators.defs.extract_complex_contents.resources.PROMPT_LABEL_NARRATIVE",
        "n",
    )
    monkeypatch.setattr(
        "orchestrators.defs.extract_complex_contents.resources.PROMPT_LABEL_TOPIC_CARD",
        "t",
    )
    monkeypatch.setattr(
        "orchestrators.defs.extract_complex_contents.resources.PROMPT_LABEL_FOLLOWUPS",
        "f",
    )
    return ExtractorRegistry(openai_api_key="k", model="gpt-4o-mini")


def test_registry_build_returns_extractor_with_3call_v1_bundle_label(tmp_path: Path, monkeypatch):
    registry = _make_registry(tmp_path, monkeypatch)
    ex = registry.build()
    assert ex.bundle_label == "3call_v1"


def test_registry_build_bundle_sha256_changes_with_prompt_content(tmp_path: Path, monkeypatch):
    """Bundle sha covers model + the three prompt texts — bumping any one
    flips the cohort-staleness signal written to queue_items.extractor_sha256."""
    base = _make_registry(tmp_path, monkeypatch).build()
    other = _make_registry(tmp_path, monkeypatch, narrative="DIFFERENT NARRATIVE").build()
    assert other.bundle_sha256 != base.bundle_sha256
    assert len(base.bundle_sha256) == 64


def test_extractor_uses_real_v1_prompt_labels():
    """narrative_v1.md / topic_card_v1.md / followups_v1.md ship in the
    package — the registry loads them without monkeypatching anything."""
    from orchestrators.defs.extract_complex_contents.def_config import (
        PROMPT_LABEL_FOLLOWUPS,
        PROMPT_LABEL_NARRATIVE,
        PROMPT_LABEL_TOPIC_CARD,
    )

    assert PROMPT_LABEL_NARRATIVE == "narrative_v1"
    assert PROMPT_LABEL_TOPIC_CARD == "topic_card_v1"
    assert PROMPT_LABEL_FOLLOWUPS == "followups_v1"

    registry = ExtractorRegistry(openai_api_key="k", model="gpt-4o-mini")
    assert "Core idea" in registry._prompt_text(PROMPT_LABEL_NARRATIVE)
    assert "PER-FIELD CONTRACTS" in registry._prompt_text(PROMPT_LABEL_TOPIC_CARD)
    assert "follow-up questions" in registry._prompt_text(PROMPT_LABEL_FOLLOWUPS)
    assert len(registry.build().bundle_sha256) == 64


# -------- QueueStoreResource --------


def test_store_roundtrip_via_real_sqlite(tmp_path: Path):
    """Smoke test — store delegates to domains.queue_store.sources; one path through."""
    store = QueueStoreResource(db_path=str(tmp_path / "q.db"))
    store.ensure_schema()
    store.upsert_fetched(
        notion_page_id="p-1",
        url="https://example.com/x",
        raw_content="raw body",
        fetch_tier="jina",
        fetch_tier_log=[{"tier": "jina"}],
        fetched_content_char_count=8,
        content_hash="h",
    )
    row = store.get_row("p-1")
    assert row is not None and row["url"] == "https://example.com/x"


# -------- FetcherResource (service-backed) --------


def _make_service_fetcher(**overrides) -> FetcherResource:
    return FetcherResource(
        service_url="http://fetcher:8000",
        timeout_s=overrides.pop("timeout_s", 30),
        allow_paid=overrides.pop("allow_paid", "true"),
        **overrides,
    )


def _fake_response(status_code: int, json_body: dict | None, *, raise_on_json: bool = False):
    mock = MagicMock()
    mock.status_code = status_code
    if raise_on_json:
        mock.json.side_effect = ValueError("not json")
        mock.text = "<html>not json</html>"
    else:
        mock.json.return_value = json_body
    return mock


def test_fetcher_returns_outcome_when_service_returns_200():
    resource = _make_service_fetcher()
    body = {
        "markdown": "# Title\n\nbody",
        "source_type": "article",
        "canonical_url": "https://example.com/x",
        "tier_used": "jina",
        "fetched_at": "2026-06-09T00:00:00Z",
        "cache_hit": False,
        "etag": "abc123",
        "tier_log": [
            {"tier": "jina", "status": 200, "chars": 5000, "error": None, "validated": True},
        ],
        "metadata": {"title": "Title"},
    }
    with patch("httpx.Client.post", return_value=_fake_response(200, body)) as post:
        result = resource.fetch_for_type("https://example.com/x", content_type="Article")
    assert post.called
    sent = post.call_args
    assert sent.kwargs["json"] == {
        "url": "https://example.com/x",
        "quality": "fast",
        "allow_paid": True,
        "force_refresh": False,
    }
    assert result.content == "# Title\n\nbody"
    assert result.tier == "jina"
    assert result.tier_log == body["tier_log"]
    assert result.title == "Title"
    assert result.extras == {"title": "Title"}
    assert result.error == ""
    assert result.transient is False


def test_fetcher_maps_502_upstream_failure_to_transient():
    resource = _make_service_fetcher()
    problem = {
        "type": "https://fetcher/errors/upstream-failure",
        "title": "All tiers failed",
        "status": 502,
        "code": "UPSTREAM_FAILURE",
        "detail": "jina:520 curl_cffi:0",
        "instance": "/v1/fetch",
        "retryable": True,
        "tier_log": [
            {"tier": "jina", "status": 520, "chars": 0, "error": "blocked", "validated": False},
        ],
    }
    with patch("httpx.Client.post", return_value=_fake_response(502, problem)):
        result = resource.fetch_for_type("https://example.com/x", content_type="Article")
    assert result.transient is True
    assert "All tiers failed" in result.error
    assert "jina:520" in result.error
    assert result.tier_log == problem["tier_log"]


def test_fetcher_maps_422_unsupported_source_to_permanent():
    resource = _make_service_fetcher()
    problem = {
        "title": "No source matches this URL",
        "status": 422,
        "code": "UNSUPPORTED_SOURCE",
        "detail": "no source matches: https://weird/",
        "retryable": False,
    }
    with patch("httpx.Client.post", return_value=_fake_response(422, problem)):
        result = resource.fetch_for_type("https://weird/", content_type="Article")
    assert result.transient is False
    assert "No source matches this URL" in result.error


def test_fetcher_maps_429_rate_limited_to_transient():
    resource = _make_service_fetcher()
    problem = {
        "title": "Per-source semaphore exhausted",
        "status": 429,
        "code": "RATE_LIMITED",
        "detail": "arxiv concurrent fetches >= 1",
        "retryable": True,
    }
    with patch("httpx.Client.post", return_value=_fake_response(429, problem)):
        result = resource.fetch_for_type("https://arxiv.org/abs/x", content_type="arXiv")
    assert result.transient is True
    assert "Per-source semaphore exhausted" in result.error


def test_fetcher_maps_400_bad_url_to_permanent():
    resource = _make_service_fetcher()
    problem = {
        "title": "Malformed URL",
        "status": 400,
        "code": "BAD_URL",
        "detail": "malformed URL: not-a-url",
        "retryable": False,
    }
    with patch("httpx.Client.post", return_value=_fake_response(400, problem)):
        result = resource.fetch_for_type("not-a-url", content_type="Article")
    assert result.transient is False
    assert "Malformed URL" in result.error


def test_fetcher_maps_504_upstream_timeout_to_transient():
    resource = _make_service_fetcher()
    problem = {
        "title": "Per-request deadline exceeded",
        "status": 504,
        "code": "UPSTREAM_TIMEOUT",
        "detail": "timeout after 30s",
        "retryable": True,
    }
    with patch("httpx.Client.post", return_value=_fake_response(504, problem)):
        result = resource.fetch_for_type("https://example.com/slow", content_type="Article")
    assert result.transient is True
    assert "Per-request deadline exceeded" in result.error


def test_fetcher_maps_connect_error_to_transient():
    resource = _make_service_fetcher()
    with patch("httpx.Client.post", side_effect=httpx.ConnectError("Connection refused")):
        result = resource.fetch_for_type("https://example.com/x", content_type="Article")
    assert result.transient is True
    assert "fetcher service unreachable" in result.error
    assert "Connection refused" in result.error


def test_fetcher_maps_read_timeout_to_transient():
    resource = _make_service_fetcher()
    with patch("httpx.Client.post", side_effect=httpx.ReadTimeout("timed out")):
        result = resource.fetch_for_type("https://example.com/x", content_type="Article")
    assert result.transient is True
    assert "fetcher request timeout" in result.error


def test_fetcher_maps_remote_protocol_error_to_transient():
    """Catches the realistic 'fetcher process restarted mid-request' shape —
    httpx.RemoteProtocolError is a TransportError, not a Connect/ReadTimeout."""
    resource = _make_service_fetcher()
    with patch("httpx.Client.post", side_effect=httpx.RemoteProtocolError("Server disconnected")):
        result = resource.fetch_for_type("https://example.com/x", content_type="Article")
    assert result.transient is True
    assert "fetcher transport error" in result.error


def test_fetcher_maps_500_internal_error_to_transient():
    """500 INTERNAL_ERROR (FetcherError base default) — retryable=False per the
    contract, but the response shape itself is still problem+json."""
    resource = _make_service_fetcher()
    problem = {
        "title": "Internal error",
        "status": 500,
        "code": "INTERNAL_ERROR",
        "detail": "KeyError: 'foo'",
        "retryable": False,
    }
    with patch("httpx.Client.post", return_value=_fake_response(500, problem)):
        result = resource.fetch_for_type("https://example.com/x", content_type="Article")
    assert result.transient is False
    assert "Internal error" in result.error


def test_fetcher_fails_loud_on_malformed_error_json():
    resource = _make_service_fetcher()
    with patch("httpx.Client.post", return_value=_fake_response(502, None, raise_on_json=True)):
        result = resource.fetch_for_type("https://example.com/x", content_type="Article")
    assert result.transient is False
    assert "malformed fetcher response" in result.error
    assert "status=502" in result.error


def test_fetcher_fails_loud_on_200_missing_markdown():
    resource = _make_service_fetcher()
    body = {
        "source_type": "article",
        "tier_used": "jina",
        "tier_log": [],
        "metadata": {},
    }
    with patch("httpx.Client.post", return_value=_fake_response(200, body)):
        result = resource.fetch_for_type("https://example.com/x", content_type="Article")
    assert result.transient is False
    assert "missing markdown" in result.error


def test_fetcher_sends_allow_paid_false_when_configured():
    resource = _make_service_fetcher(allow_paid="false")
    body = {
        "markdown": "x",
        "source_type": "article",
        "canonical_url": "u",
        "tier_used": "jina",
        "fetched_at": "t",
        "cache_hit": False,
        "etag": "e",
        "tier_log": [],
        "metadata": {},
    }
    with patch("httpx.Client.post", return_value=_fake_response(200, body)) as post:
        resource.fetch_for_type("https://example.com/x", content_type="Article")
    assert post.call_args.kwargs["json"]["allow_paid"] is False


def test_fetcher_passes_content_type_without_using_it():
    """`content_type` arg is kept for asset-side API stability but unused —
    the service determines source from the URL itself. Different types must
    produce identical request payloads for the same URL."""
    resource = _make_service_fetcher()
    body = {
        "markdown": "x",
        "source_type": "article",
        "canonical_url": "u",
        "tier_used": "jina",
        "fetched_at": "t",
        "cache_hit": False,
        "etag": "e",
        "tier_log": [],
        "metadata": {},
    }
    with patch("httpx.Client.post", return_value=_fake_response(200, body)) as post:
        resource.fetch_for_type("https://x/", content_type="YouTube")
        resource.fetch_for_type("https://x/", content_type="arXiv")
        resource.fetch_for_type("https://x/", content_type="Article")
    payloads = [call.kwargs["json"] for call in post.call_args_list]
    assert payloads[0] == payloads[1] == payloads[2]
    assert "source" not in payloads[0]


# -------- FetcherResource.structure() --------


def test_fetcher_structure_calls_v1_structure_endpoint():
    resource = _make_service_fetcher()
    body = {
        "markdown": "# Clean\n\nbody",
        "kind": "structured",
        "canonical_url": "https://example.com/a",
        "tier_used": "structurer:gpt-4.1-mini",
        "fetched_at": "2026-06-10T00:00:00Z",
        "cache_hit": False,
        "etag": "",
        "tier_log": [],
        "metadata": {"model": "gpt-4.1-mini", "prompt_version": "v1"},
    }
    with patch("httpx.Client.post", return_value=_fake_response(200, body)) as post:
        result = resource.structure(
            "raw paste text", title="Real Title", source_url="https://example.com/a"
        )
    assert post.called
    endpoint = post.call_args.args[0]
    assert endpoint.endswith("/v1/structure")
    sent = post.call_args.kwargs["json"]
    assert sent["raw_content"] == "raw paste text"
    assert sent["title"] == "Real Title"
    assert sent["source_url"] == "https://example.com/a"
    assert result.content == "# Clean\n\nbody"
    assert result.tier == "structurer:gpt-4.1-mini"


def test_fetcher_structure_maps_wire_fields_to_orchestrator_fetch_result():
    """Round-trip the tier_log entries field-for-field, not just length-equal."""
    resource = _make_service_fetcher()
    tier_log = [
        {"tier": "trafilatura", "status": None, "chars": 0, "error": "empty", "validated": False},
        {
            "tier": "structurer:gpt-4.1-mini",
            "status": None,
            "chars": 120,
            "error": None,
            "validated": True,
        },
    ]
    body = {
        "markdown": "out",
        "kind": "structured",
        "canonical_url": "https://example.com/a",
        "tier_used": "structurer:gpt-4.1-mini",
        "fetched_at": "2026-06-10T00:00:00Z",
        "cache_hit": False,
        "etag": "",
        "tier_log": tier_log,
        "metadata": {"model": "gpt-4.1-mini", "prompt_version": "v1"},
    }
    with patch("httpx.Client.post", return_value=_fake_response(200, body)):
        result = resource.structure("raw", source_url="https://example.com/a")
    assert result.tier_log == tier_log
    assert result.tier_log[0]["error"] == "empty"
    assert result.tier_log[1]["chars"] == 120


def test_fetcher_structure_maps_502_problem_to_transient_failure():
    resource = _make_service_fetcher()
    problem = {
        "title": "Structurer cascade exhausted",
        "status": 502,
        "code": "STRUCTURER_UPSTREAM_FAILURE",
        "detail": "openai timeout; ollama down",
        "retryable": True,
        "tier_log": [
            {
                "tier": "structurer",
                "status": None,
                "chars": 0,
                "error": "timeout",
                "validated": False,
            },
        ],
    }
    with patch("httpx.Client.post", return_value=_fake_response(502, problem)):
        result = resource.structure("raw", source_url="https://example.com/a")
    assert result.content == ""
    assert result.transient is True
    assert "Structurer cascade exhausted" in result.error
    assert result.tier_log == problem["tier_log"]


def test_fetcher_structure_maps_503_problem_to_permanent_failure():
    resource = _make_service_fetcher()
    problem = {
        "title": "Structurer not configured",
        "status": 503,
        "code": "STRUCTURER_UNCONFIGURED",
        "detail": "no API keys configured",
        "retryable": False,
        "tier_log": [],
    }
    with patch("httpx.Client.post", return_value=_fake_response(503, problem)):
        result = resource.structure("raw", source_url="https://example.com/a")
    assert result.transient is False
    assert "Structurer not configured" in result.error
