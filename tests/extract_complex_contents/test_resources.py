"""Tests for extract_complex_contents resources.

Mocks the external SDKs (notion-client, anthropic, requests, curl-cffi) at
the import location in their respective modules. Covers what the asset bodies
depend on:
- Notion query/get/update payload shapes
- Fetcher dispatch (YouTube, arXiv, article cascade)
- Extractor prompt loading + JSON parsing
- Store thin delegation
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

from orchestrators.defs.extract_complex_contents.fetchers import article, arxiv, youtube
from orchestrators.defs.extract_complex_contents.resources import (
    ExtractionUsage,
    ExtractorResource,
    ExtractQueueStore,
    FetcherResource,
    NotionResource,
    _parse_topic_card,
)

# -------- _parse_topic_card --------


def test_parse_topic_card_extracts_fenced_json():
    text = """
some preamble

```json
{
  "title": "JEPA talk",
  "core_mechanism": "predict abstract representations",
  "best_example": "Meta's I-JEPA matched contrastive baselines",
  "second_example": "DINO-WM extended to video",
  "transferable_pattern": "predict in learned latent space",
  "main_tension": "Pixel prediction vs abstraction loss",
  "candidate_tie_backs": ["LeCun 2022 position paper"]
}
```

trailing text"""
    out = _parse_topic_card(text)
    assert out["extracted_title"] == "JEPA talk"
    assert out["core_mechanism"].startswith("predict")
    assert out["candidate_tie_backs"] == ["LeCun 2022 position paper"]


def test_parse_topic_card_renames_title_to_extracted_title():
    """v5 prompt emits 'title'; schema uses 'extracted_title'."""
    text = '```json\n{"title": "X"}\n```'
    out = _parse_topic_card(text)
    assert out["extracted_title"] == "X"
    assert "title" not in out


def test_parse_topic_card_omits_unknown_keys():
    text = '```json\n{"title": "X", "extra_field": "ignored"}\n```'
    out = _parse_topic_card(text)
    assert "extra_field" not in out


# -------- NotionResource --------


def _make_notion_with_mocked_client() -> tuple[NotionResource, MagicMock]:
    resource = NotionResource(
        integration_token="secret_x",
        queue_db_id="db-123",
        queue_data_source_id="ds-456",
    )
    mock_client = MagicMock()
    with patch.object(NotionResource, "_client", return_value=mock_client):
        pass
    # Re-patch for actual use — caller wraps with patch.object too.
    return resource, mock_client


def test_notion_query_queue_filters_by_fetching_and_content_type():
    resource = NotionResource(
        integration_token="secret_x",
        queue_db_id="db-123",
        queue_data_source_id="ds-456",
    )
    fake_client = MagicMock()
    fake_client.data_sources.query.return_value = {"results": [{"id": "p-1"}]}
    with patch.object(NotionResource, "_client", return_value=fake_client):
        rows = resource.query_queue(page_size=2, supported_content_types=("YouTube", "arXiv"))
    fake_client.data_sources.query.assert_called_once_with(
        data_source_id="ds-456",
        filter={
            "and": [
                {"property": "Status", "select": {"equals": "Fetching"}},
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


def test_notion_query_queue_single_type_uses_flat_clause():
    resource = NotionResource(
        integration_token="secret_x",
        queue_db_id="db-123",
        queue_data_source_id="ds-456",
    )
    fake_client = MagicMock()
    fake_client.data_sources.query.return_value = {"results": []}
    with patch.object(NotionResource, "_client", return_value=fake_client):
        resource.query_queue(page_size=1, supported_content_types=("YouTube",))
    call_filter = fake_client.data_sources.query.call_args.kwargs["filter"]
    type_clause = call_filter["and"][1]
    assert type_clause == {"property": "Content Type", "select": {"equals": "YouTube"}}


def test_notion_update_status_writes_select_property():
    resource = NotionResource(
        integration_token="secret_x",
        queue_db_id="db-123",
        queue_data_source_id="ds-456",
    )
    fake_client = MagicMock()
    with patch.object(NotionResource, "_client", return_value=fake_client):
        resource.update_status("page-id", "Ready")
    fake_client.pages.update.assert_called_once_with(
        page_id="page-id",
        properties={"Status": {"select": {"name": "Ready"}}},
    )


def test_notion_update_status_failed_writes_status_and_error():
    resource = NotionResource(
        integration_token="secret_x",
        queue_db_id="db-123",
        queue_data_source_id="ds-456",
    )
    fake_client = MagicMock()
    with patch.object(NotionResource, "_client", return_value=fake_client):
        resource.update_status_failed("page-id", "fetch returned 403")
    args, kwargs = fake_client.pages.update.call_args
    assert kwargs["page_id"] == "page-id"
    props = kwargs["properties"]
    assert props["Status"] == {"select": {"name": "Failed"}}
    assert props["Error"]["rich_text"][0]["text"]["content"] == "fetch returned 403"


def test_notion_update_status_failed_truncates_long_errors():
    resource = NotionResource(
        integration_token="secret_x",
        queue_db_id="db-123",
        queue_data_source_id="ds-456",
    )
    fake_client = MagicMock()
    with patch.object(NotionResource, "_client", return_value=fake_client):
        resource.update_status_failed("page-id", "x" * 5000)
    props = fake_client.pages.update.call_args.kwargs["properties"]
    assert len(props["Error"]["rich_text"][0]["text"]["content"]) == 1900


def test_notion_get_status_reads_select_name():
    resource = NotionResource(
        integration_token="secret_x",
        queue_db_id="db-123",
        queue_data_source_id="ds-456",
    )
    fake_client = MagicMock()
    fake_client.pages.retrieve.return_value = {
        "properties": {"Status": {"select": {"name": "Ready"}}}
    }
    with patch.object(NotionResource, "_client", return_value=fake_client):
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


# -------- ExtractorResource --------


def test_extractor_loads_prompt_from_file_with_label(tmp_path: Path, monkeypatch):
    fake_dir = tmp_path / "prompts"
    fake_dir.mkdir()
    (fake_dir / "v9_test.md").write_text("THIS IS THE PROMPT BODY")
    monkeypatch.setattr(
        "orchestrators.defs.extract_complex_contents.resources._PROMPTS_DIR",
        fake_dir,
    )
    resource = ExtractorResource(
        anthropic_api_key="k",
        model="anthropic/claude-opus-4-7",
        prompt_label="v9_test",
    )
    assert resource.prompt_text == "THIS IS THE PROMPT BODY"
    assert resource.prompt_sha256 == _sha256_hex("THIS IS THE PROMPT BODY")


def test_extractor_uses_real_v5_prompt_label():
    """v5_kp_copy_2026_05_31.md exists in the package — extractor can load it."""
    resource = ExtractorResource(
        anthropic_api_key="k",
        model="anthropic/claude-opus-4-7",
        prompt_label="v5_kp_copy_2026_05_31",
    )
    assert "Topic Card" in resource.prompt_text
    assert len(resource.prompt_sha256) == 64


def test_extractor_extract_sends_prompt_and_parses_json(tmp_path: Path, monkeypatch):
    fake_dir = tmp_path / "prompts"
    fake_dir.mkdir()
    (fake_dir / "v_test.md").write_text("system prompt here")
    monkeypatch.setattr(
        "orchestrators.defs.extract_complex_contents.resources._PROMPTS_DIR",
        fake_dir,
    )
    resource = ExtractorResource(
        anthropic_api_key="k",
        model="anthropic/claude-opus-4-7",
        prompt_label="v_test",
    )

    fake_response = MagicMock()
    fake_response.content = [MagicMock(text='```json\n{"title": "T", "core_mechanism": "M"}\n```')]
    fake_response.usage = MagicMock(input_tokens=4000, output_tokens=600)
    fake_client = MagicMock()
    fake_client.messages.create.return_value = fake_response

    with patch(
        "orchestrators.defs.extract_complex_contents.resources.Anthropic",
        return_value=fake_client,
    ):
        extraction, usage = resource.extract("source article body")

    assert extraction["extracted_title"] == "T"
    assert extraction["core_mechanism"] == "M"
    assert isinstance(usage, ExtractionUsage)
    assert usage.input_tokens == 4000
    assert usage.output_tokens == 600

    create_call = fake_client.messages.create.call_args.kwargs
    assert create_call["model"] == "anthropic/claude-opus-4-7"
    assert create_call["system"] == "system prompt here"
    assert create_call["messages"] == [{"role": "user", "content": "source article body"}]


# -------- ExtractQueueStore --------


def test_store_roundtrip_via_real_sqlite(tmp_path: Path):
    """Smoke test — store delegates to domains.raw_store.queue; one path through."""
    store = ExtractQueueStore(db_path=str(tmp_path / "q.db"))
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
    resource = FetcherResource(pi_socks5_url="socks5://pi:1080")
    sentinel = MagicMock(return_value=MagicMock())
    with patch.object(arxiv, "fetch", sentinel):
        resource.fetch_for_type("https://arxiv.org/abs/2310.06770", content_type="arXiv")
    sentinel.assert_called_once_with("https://arxiv.org/abs/2310.06770")


def test_fetcher_dispatch_unknown_type_falls_back_to_article():
    resource = FetcherResource(pi_socks5_url="socks5://pi:1080")
    sentinel = MagicMock(return_value=MagicMock())
    with patch.object(article, "fetch", sentinel):
        resource.fetch_for_type("https://example.com/post", content_type="Article")
    sentinel.assert_called_once()
    call_url = sentinel.call_args.args[0]
    assert call_url == "https://example.com/post"


# -------- helpers --------


def _sha256_hex(s: str) -> str:
    import hashlib

    return hashlib.sha256(s.encode()).hexdigest()
