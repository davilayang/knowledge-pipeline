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

from orchestrators.defs.extract_complex_contents.fetchers import article, arxiv, youtube
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


# -------- FetcherResource --------


def test_fetcher_returns_jina_when_above_floor():
    resource = FetcherResource(
        pi_socks5_url="socks5://pi:1080",
        impersonate_profile="safari17_0",
        jina_floor_chars=100,
    )
    with (
        patch(
            "orchestrators.defs.extract_complex_contents.fetchers.article._jina_fetch",
            return_value=("a" * 200, 200, None),
        ) as jina_mock,
        patch(
            "orchestrators.defs.extract_complex_contents.fetchers.article._curl_cffi_fetch"
        ) as curl_mock,
    ):
        result = resource.fetch("https://example.com/x")
    jina_mock.assert_called_once()
    curl_mock.assert_not_called()
    assert result.tier == "jina"
    assert len(result.content) == 200
    assert result.tier_log[0]["tier"] == "jina"
    assert result.tier_log[0]["chars"] == 200


def test_fetcher_falls_through_to_curl_cffi_when_jina_short():
    resource = FetcherResource(
        pi_socks5_url="socks5://pi:1080",
        impersonate_profile="safari17_0",
        jina_floor_chars=2000,
    )
    with (
        patch(
            "orchestrators.defs.extract_complex_contents.fetchers.article._jina_fetch",
            return_value=("short", 200, None),
        ),
        patch(
            "orchestrators.defs.extract_complex_contents.fetchers.article._curl_cffi_fetch",
            return_value=("<html><body>real article body</body></html>", 200, None),
        ),
        patch(
            "orchestrators.defs.extract_complex_contents.fetchers.article._trafilatura_extract",
            return_value="extracted markdown body, multiple paragraphs.",
        ),
    ):
        result = resource.fetch("https://example.com/x")
    assert result.tier == "curl_cffi"
    assert result.content == "extracted markdown body, multiple paragraphs."
    assert [entry["tier"] for entry in result.tier_log] == ["jina", "curl_cffi"]


def test_fetcher_tier_log_records_errors_from_both_tiers():
    resource = FetcherResource(
        pi_socks5_url="socks5://pi:1080",
        impersonate_profile="safari17_0",
        jina_floor_chars=100,
    )
    with (
        patch(
            "orchestrators.defs.extract_complex_contents.fetchers.article._jina_fetch",
            return_value=("", None, "ConnectionError"),
        ),
        patch(
            "orchestrators.defs.extract_complex_contents.fetchers.article._curl_cffi_fetch",
            return_value=("", 403, None),
        ),
        patch(
            "orchestrators.defs.extract_complex_contents.fetchers.article._trafilatura_extract",
            return_value="",
        ),
    ):
        result = resource.fetch("https://example.com/x")
    assert result.tier_log[0]["error"] == "ConnectionError"
    assert result.tier_log[1]["status"] == 403
    assert result.content == ""


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


# -------- FetcherResource dispatch --------


def test_fetcher_dispatch_youtube_calls_youtube_module():
    resource = FetcherResource(pi_socks5_url="socks5://pi:1080")
    sentinel = MagicMock(return_value=MagicMock())
    with patch.object(youtube, "fetch", sentinel):
        resource.fetch_for_type("https://youtu.be/abcdefghijk", content_type="YouTube")
    sentinel.assert_called_once_with("https://youtu.be/abcdefghijk", proxy_url=None)


def test_fetcher_dispatch_arxiv_calls_arxiv_module():
    resource = FetcherResource(
        pi_socks5_url="socks5://pi:1080",
        llama_cloud_api_key="llama-key",
        llama_parse_tier="agentic_plus",
    )
    sentinel = MagicMock(return_value=MagicMock())
    with patch.object(arxiv, "fetch", sentinel):
        resource.fetch_for_type("https://arxiv.org/abs/2310.06770", content_type="arXiv")
    sentinel.assert_called_once_with(
        "https://arxiv.org/abs/2310.06770",
        llama_cloud_api_key="llama-key",
        llama_cloud_base_url="https://api.cloud.eu.llamaindex.ai",
        llama_parse_tier="agentic_plus",
    )


def test_fetcher_dispatch_unknown_type_falls_back_to_article():
    resource = FetcherResource(pi_socks5_url="socks5://pi:1080")
    sentinel = MagicMock(return_value=MagicMock())
    with patch.object(article, "fetch", sentinel):
        resource.fetch_for_type("https://example.com/post", content_type="Article")
    sentinel.assert_called_once()
    call_url = sentinel.call_args.args[0]
    assert call_url == "https://example.com/post"
