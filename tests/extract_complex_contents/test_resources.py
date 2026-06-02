"""Tests for extract_complex_contents resources.

Mocks the external SDKs (notion-client, openai, requests, curl-cffi) at
the import location in their respective modules. Covers what the asset bodies
depend on:
- Notion query/get/update payload shapes
- Fetcher dispatch (YouTube, arXiv, article cascade)
- ExtractorRegistry prompt routing + JSON parsing
- Store thin delegation
"""

import hashlib
from pathlib import Path
from unittest.mock import MagicMock, patch

from orchestrators.defs.extract_complex_contents.extractors import ExtractionUsage
from orchestrators.defs.extract_complex_contents.extractors.openai_single_shot import (
    _parse_topic_card,
)
from orchestrators.defs.extract_complex_contents.fetchers import article, arxiv, youtube
from orchestrators.defs.extract_complex_contents.resources import (
    ExtractorRegistry,
    FetcherResource,
)
from orchestrators.defs.shared.queue_resources import NotionQueueResource, QueueStoreResource

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
    pa: str = "article prompt",
    py: str = "youtube prompt",
    pax: str = "arxiv prompt",
) -> "ExtractorRegistry":
    fake_dir = tmp_path / "prompts"
    fake_dir.mkdir(exist_ok=True)
    (fake_dir / "pa.md").write_text(pa)
    (fake_dir / "py.md").write_text(py)
    (fake_dir / "pax.md").write_text(pax)
    monkeypatch.setattr(
        "orchestrators.defs.extract_complex_contents.resources._PROMPTS_DIR",
        fake_dir,
    )
    return ExtractorRegistry(
        openai_api_key="k",
        model="gpt-4o-mini",
        prompt_label_article="pa",
        prompt_label_youtube="py",
        prompt_label_arxiv="pax",
    )


def test_strategy_for_article_uses_article_prompt(tmp_path: Path, monkeypatch):
    registry = _make_registry(tmp_path, monkeypatch)
    assert registry.prompt_label("Article") == "pa"
    assert registry.prompt_sha256("Article") == _sha256_hex("article prompt")


def test_strategy_for_youtube_uses_youtube_prompt(tmp_path: Path, monkeypatch):
    registry = _make_registry(tmp_path, monkeypatch)
    assert registry.prompt_label("YouTube") == "py"
    assert registry.prompt_sha256("YouTube") == _sha256_hex("youtube prompt")


def test_strategy_for_arxiv_uses_arxiv_prompt(tmp_path: Path, monkeypatch):
    registry = _make_registry(tmp_path, monkeypatch)
    assert registry.prompt_label("arXiv") == "pax"
    assert registry.prompt_sha256("arXiv") == _sha256_hex("arxiv prompt")


def test_strategy_for_unknown_type_falls_back_to_article(tmp_path: Path, monkeypatch):
    registry = _make_registry(tmp_path, monkeypatch)
    assert registry.prompt_label("UnknownType") == "pa"
    assert registry.prompt_sha256("UnknownType") == _sha256_hex("article prompt")


def test_extract_invokes_openai_with_per_type_prompt(tmp_path: Path, monkeypatch):
    registry = _make_registry(tmp_path, monkeypatch)

    fake_usage = MagicMock(prompt_tokens=500, completion_tokens=100)
    fake_message = MagicMock(
        content='```json\n{"title": "arXiv Paper", "core_mechanism": "CM"}\n```'
    )
    fake_choice = MagicMock(message=fake_message)
    fake_response = MagicMock(choices=[fake_choice], usage=fake_usage)
    fake_completions = MagicMock()
    fake_completions.create.return_value = fake_response
    fake_chat = MagicMock(completions=fake_completions)
    fake_client = MagicMock(chat=fake_chat)

    with patch(
        "orchestrators.defs.extract_complex_contents.extractors.openai_single_shot.openai.OpenAI",
        return_value=fake_client,
    ):
        extraction, usage = registry.extract("paper body", content_type="arXiv")

    assert extraction["extracted_title"] == "arXiv Paper"
    assert extraction["core_mechanism"] == "CM"
    assert usage.input_tokens == 500
    assert usage.output_tokens == 100

    create_kwargs = fake_completions.create.call_args.kwargs
    system_msg = next(m for m in create_kwargs["messages"] if m["role"] == "system")
    assert system_msg["content"] == "arxiv prompt"


def test_protocol_swap(tmp_path: Path, monkeypatch):
    """Replacing _strategy_for returns the sentinel extraction — asset path is protocol-bound."""
    registry = _make_registry(tmp_path, monkeypatch)
    sentinel_extraction = {
        "extracted_title": "SENTINEL",
        "core_mechanism": None,
        "best_example": None,
        "second_example": None,
        "transferable_pattern": None,
        "main_tension": None,
        "candidate_tie_backs": [],
    }
    sentinel_usage = ExtractionUsage(input_tokens=1, output_tokens=1)

    class FakeExtractor:
        def extract(self, content: str, *, content_type: str) -> tuple[dict, ExtractionUsage]:
            return sentinel_extraction, sentinel_usage

    monkeypatch.setattr(registry, "_strategy_for", lambda ct: FakeExtractor())
    extraction, usage = registry.extract("x", content_type="YouTube")
    assert extraction["extracted_title"] == "SENTINEL"
    assert usage.input_tokens == 1


def test_extractor_uses_real_v5_article_prompt_label():
    """v5_article_kp_copy_2026_05_31.md exists in the package — registry can load it."""
    registry = ExtractorRegistry(
        openai_api_key="k",
        model="gpt-4o-mini",
        prompt_label_article="v5_article_kp_copy_2026_05_31",
        prompt_label_youtube="v5_youtube_kp_copy_2026_06_01",
        prompt_label_arxiv="v5_arxiv_kp_copy_2026_06_01",
    )
    assert "Topic Card" in registry._prompt_text("Article")
    assert len(registry.prompt_sha256("Article")) == 64


# -------- QueueStoreResource --------


def test_store_roundtrip_via_real_sqlite(tmp_path: Path):
    """Smoke test — store delegates to domains.raw_store.queue; one path through."""
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


# -------- helpers --------


def _sha256_hex(s: str) -> str:

    return hashlib.sha256(s.encode()).hexdigest()
