"""Tests for the triaged asset."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import dagster as dg
import pytest
from orchestrators.defs.shared.queue_resources import QueueStoreResource
from orchestrators.defs.triage_knowledge_queue.assets import triaged
from orchestrators.defs.triage_knowledge_queue.def_config import queue_items_partition_def
from orchestrators.defs.triage_knowledge_queue.url_meta import UrlMeta


@pytest.fixture(autouse=True)
def _no_network():
    """Default: url_meta returns empty UrlMeta with redirected_url = input. Tests
    that want a specific UrlMeta should override via _patch_fetch."""

    def _empty(url: str, **kwargs):
        return UrlMeta(redirected_url=url, title=None, description=None)

    with patch(
        "orchestrators.defs.triage_knowledge_queue.assets.fetch_url_meta",
        side_effect=_empty,
    ) as fake:
        yield fake


def _patch_fetch(meta: UrlMeta):
    return patch(
        "orchestrators.defs.triage_knowledge_queue.assets.fetch_url_meta",
        return_value=meta,
    )


def _instance_with_partition(page_id: str) -> dg.DagsterInstance:
    instance = dg.DagsterInstance.ephemeral()
    instance.add_dynamic_partitions(queue_items_partition_def.name, [page_id])
    return instance


def _materialize(
    *,
    partition_key: str,
    resources: dict,
    url: str,
    content_type: str | None = None,
    name: str | None = None,
):
    instance = _instance_with_partition(partition_key)
    op_config: dict = {"url": url}
    if content_type is not None:
        op_config["content_type"] = content_type
    if name is not None:
        op_config["name"] = name
    return dg.materialize(
        [triaged],
        partition_key=partition_key,
        resources=resources,
        instance=instance,
        tags={"notion_page_id": partition_key},
        run_config={"ops": {"triage_knowledge_queue__triaged": {"config": op_config}}},
    )


def _get_metadata(result) -> dict:
    mat_events = [e for e in result.all_events if e.event_type_value == "ASSET_MATERIALIZATION"]
    assert mat_events
    return mat_events[0].materialization.metadata


def _resources(tmp_path: Path):
    store = QueueStoreResource(db_path=str(tmp_path / "q.db"))
    notion = MagicMock()
    return {"triage_notion": notion, "triage_store": store}, notion


# -------- classification metadata --------


def test_triaged_returns_youtube_for_youtube_url(tmp_path: Path):
    resources, _ = _resources(tmp_path)
    result = _materialize(
        partition_key="p-1",
        resources=resources,
        url="https://youtube.com/watch?v=xx12345abcd",
    )
    assert result.success
    metadata = _get_metadata(result)
    assert metadata["content_type"].text == "YouTube"


def test_triaged_returns_article_for_blog_url(tmp_path: Path):
    resources, _ = _resources(tmp_path)
    result = _materialize(
        partition_key="p-1",
        resources=resources,
        url="https://blog.example.com/post",
    )
    assert result.success
    metadata = _get_metadata(result)
    assert metadata["content_type"].text == "Article"


# -------- routing side effects --------


def test_triaged_writes_status_fetching_for_youtube(tmp_path: Path):
    resources, notion = _resources(tmp_path)
    result = _materialize(
        partition_key="p-1",
        resources=resources,
        url="https://youtube.com/watch?v=xx12345abcd",
    )
    assert result.success
    notion.write_triaged.assert_called_once()
    call_kwargs = notion.write_triaged.call_args.kwargs
    assert call_kwargs["status_after"] == "Fetching"
    assert call_kwargs["content_type"] == "YouTube"


def test_triaged_writes_status_fetching_for_article(tmp_path: Path):
    """Single-tier: Article URLs now flow to Fetching too (the fetcher service's
    article handler claims them via the catch-all match)."""
    resources, notion = _resources(tmp_path)
    result = _materialize(
        partition_key="p-1",
        resources=resources,
        url="https://blog.example.com/post",
    )
    assert result.success
    call_kwargs = notion.write_triaged.call_args.kwargs
    assert call_kwargs["status_after"] == "Fetching"
    assert call_kwargs["content_type"] == "Article"


def test_triaged_writes_fetched_title_to_notion_when_config_name_empty(tmp_path: Path):
    """config.name unset + fetched title present → triage seeds Name in Notion."""
    resources, notion = _resources(tmp_path)
    meta = UrlMeta(
        redirected_url="https://blog.example.com/post",
        title="Fetched Title",
        description=None,
    )
    with _patch_fetch(meta):
        result = _materialize(
            partition_key="p-1",
            resources=resources,
            url="https://blog.example.com/post",
        )
    assert result.success
    kwargs = notion.write_triaged.call_args.kwargs
    assert kwargs.get("name") == "Fetched Title"


def test_triaged_does_not_overwrite_existing_name(tmp_path: Path):
    """config.name set → triage leaves Notion's Name alone, regardless of fetch."""
    resources, notion = _resources(tmp_path)
    meta = UrlMeta(
        redirected_url="https://blog.example.com/post",
        title="Fetched Title",
        description=None,
    )
    with _patch_fetch(meta):
        result = _materialize(
            partition_key="p-1",
            resources=resources,
            url="https://blog.example.com/post",
            name="User-set Name",
        )
    assert result.success
    kwargs = notion.write_triaged.call_args.kwargs
    assert kwargs.get("name") is None


def test_triaged_seeds_name_when_notion_default_new_page_pattern(tmp_path: Path):
    """Notion auto-assigns "New <db_name> page" to fresh rows — treat that
    as blank-equivalent so triage replaces it with the fetched title."""
    resources, notion = _resources(tmp_path)
    meta = UrlMeta(
        redirected_url="https://blog.example.com/post",
        title="Real Title",
        description=None,
    )
    with _patch_fetch(meta):
        result = _materialize(
            partition_key="p-1",
            resources=resources,
            url="https://blog.example.com/post",
            name="New queued page",
        )
    assert result.success
    kwargs = notion.write_triaged.call_args.kwargs
    assert kwargs.get("name") == "Real Title"


def test_triaged_seeds_name_when_notion_default_untitled(tmp_path: Path):
    """The other common Notion auto-default — "Untitled" — also counts as blank."""
    resources, notion = _resources(tmp_path)
    meta = UrlMeta(
        redirected_url="https://blog.example.com/post",
        title="Real Title",
        description=None,
    )
    with _patch_fetch(meta):
        result = _materialize(
            partition_key="p-1",
            resources=resources,
            url="https://blog.example.com/post",
            name="Untitled",
        )
    assert result.success
    kwargs = notion.write_triaged.call_args.kwargs
    assert kwargs.get("name") == "Real Title"


def test_triaged_preserves_real_name_that_starts_with_new(tmp_path: Path):
    """Defensive: don't false-positive on user-typed names that happen to
    start with "New" (e.g. "New trends in voice agents"). The
    "New <word> page" pattern only matches when the WHOLE string fits."""
    resources, notion = _resources(tmp_path)
    meta = UrlMeta(
        redirected_url="https://blog.example.com/post",
        title="Different Fetched Title",
        description=None,
    )
    with _patch_fetch(meta):
        result = _materialize(
            partition_key="p-1",
            resources=resources,
            url="https://blog.example.com/post",
            name="New trends in voice agents",
        )
    assert result.success
    kwargs = notion.write_triaged.call_args.kwargs
    assert kwargs.get("name") is None


def test_triaged_writes_fetched_description_to_notion(tmp_path: Path):
    resources, notion = _resources(tmp_path)
    meta = UrlMeta(
        redirected_url="https://blog.example.com/post",
        title=None,
        description="A short blurb of the post.",
    )
    with _patch_fetch(meta):
        result = _materialize(
            partition_key="p-1",
            resources=resources,
            url="https://blog.example.com/post",
        )
    assert result.success
    kwargs = notion.write_triaged.call_args.kwargs
    assert kwargs.get("description") == "A short blurb of the post."


def test_triaged_succeeds_with_empty_meta_on_fetch_failure(tmp_path: Path):
    """fetch_url_meta returning empty meta (network error, non-html, etc.) →
    triage classifies + writes Status normally, just without name/description."""
    resources, notion = _resources(tmp_path)
    result = _materialize(
        partition_key="p-1",
        resources=resources,
        url="https://blog.example.com/post",
    )
    assert result.success
    kwargs = notion.write_triaged.call_args.kwargs
    assert kwargs.get("name") is None
    assert kwargs.get("description") is None
    assert kwargs["status_after"] == "Fetching"


def test_triaged_uses_redirected_url_for_classification_after_redirect(tmp_path: Path):
    """A click-tracker URL that redirects to youtube.com → classified as YouTube,
    not Article. The redirect resolution happens in url_meta; triage consumes
    redirected_url for downstream classification + canonicalization."""
    resources, _ = _resources(tmp_path)
    meta = UrlMeta(
        redirected_url="https://youtube.com/watch?v=abc123",
        title=None,
        description=None,
    )
    with _patch_fetch(meta):
        result = _materialize(
            partition_key="p-1",
            resources=resources,
            url="https://t.co/shortened",
        )
    assert result.success
    metadata = _get_metadata(result)
    assert metadata["content_type"].text == "YouTube"


# -------- user override --------


def test_triaged_respects_user_set_content_type(tmp_path: Path):
    """A blog-looking URL with content_type=YouTube override → treated as YouTube."""
    resources, notion = _resources(tmp_path)
    result = _materialize(
        partition_key="p-1",
        resources=resources,
        url="https://blog.example.com/post",
        content_type="YouTube",
    )
    assert result.success
    metadata = _get_metadata(result)
    assert metadata["content_type"].text == "YouTube"
    assert metadata["content_type_source"].text == "notion"
    assert notion.write_triaged.call_args.kwargs["status_after"] == "Fetching"


def test_triaged_falls_back_to_classifier_on_typo_content_type(tmp_path: Path):
    """User typo'd content_type → classifier wins, source = 'classified'."""
    resources, _ = _resources(tmp_path)
    result = _materialize(
        partition_key="p-1",
        resources=resources,
        url="https://youtube.com/watch?v=abc",
        content_type="Youtub",  # typo
    )
    assert result.success
    metadata = _get_metadata(result)
    assert metadata["content_type"].text == "YouTube"  # classifier from URL
    assert metadata["content_type_source"].text == "classified"


def test_triaged_falls_back_to_classifier_on_empty_content_type(tmp_path: Path):
    """No content_type override → classify from URL."""
    resources, _ = _resources(tmp_path)
    result = _materialize(
        partition_key="p-1",
        resources=resources,
        url="https://arxiv.org/abs/2401.12345",
    )
    assert result.success
    metadata = _get_metadata(result)
    assert metadata["content_type"].text == "arXiv"
    assert metadata["content_type_source"].text == "classified"


def test_triaged_passes_name_through_to_metadata(tmp_path: Path):
    """name is metadata-only — appears on the run; Notion's Name is not
    overwritten when config.name is set."""
    resources, notion = _resources(tmp_path)
    result = _materialize(
        partition_key="p-1",
        resources=resources,
        url="https://example.com/post",
        name="A great article",
    )
    assert result.success
    metadata = _get_metadata(result)
    assert metadata["name"].text == "A great article"
    kwargs = notion.write_triaged.call_args.kwargs
    assert kwargs.get("name") is None


def test_triaged_persists_canonical_url_to_store_and_notion(tmp_path: Path):
    """Canonical URL goes to both queue.db (NA reads it for kp_queue_cache)
    and Notion's `Canonical URL` field (UI-visible debug surface, text-typed
    on purpose — see write_triaged docstring)."""
    resources, notion = _resources(tmp_path)
    dirty_url = "https://example.com/p?utm_source=newsletter&id=42"
    result = _materialize(
        partition_key="p-1",
        resources=resources,
        url=dirty_url,
    )
    assert result.success
    from domains.queue_store import sources as queue_db

    row = queue_db.get_row(db_path=resources["triage_store"].db_path, notion_page_id="p-1")
    assert row is not None
    assert row["canonical_url"] == "https://example.com/p"
    assert notion.write_triaged.call_args.kwargs["canonical_url"] == "https://example.com/p"


def test_triaged_passes_added_at_iso_through_to_notion(tmp_path: Path):
    """added_at_iso from TriageInput → forwarded to write_triaged unchanged."""
    resources, notion = _resources(tmp_path)
    instance = _instance_with_partition("p-1")
    result = dg.materialize(
        [triaged],
        partition_key="p-1",
        resources=resources,
        instance=instance,
        tags={"notion_page_id": "p-1"},
        run_config={
            "ops": {
                "triage_knowledge_queue__triaged": {
                    "config": {
                        "url": "https://example.com/post",
                        "added_at_iso": "2026-06-02T08:21:00.000Z",
                    }
                }
            }
        },
    )
    assert result.success
    assert notion.write_triaged.call_args.kwargs["added_at_iso"] == "2026-06-02T08:21:00.000Z"


# -------- canonical_url dedup --------


def _seed_existing_triaged(
    store: QueueStoreResource,
    *,
    page_id: str,
    canonical_url: str,
) -> None:
    """Seed queue.db as if a prior triage row already exists for `page_id`.
    Used to simulate the "second capture of an already-queued URL" case."""
    from domains.queue_store import sources as queue_db

    queue_db.create_schema(db_path=Path(store.db_path))
    queue_db.upsert_triaged(
        db_path=Path(store.db_path),
        notion_page_id=page_id,
        url=canonical_url,
        canonical_url=canonical_url,
        content_type="Article",
    )


def test_triaged_flags_duplicate_canonical_url_as_skipped(tmp_path: Path):
    """Second capture of an already-triaged canonical_url → Notion row gets
    Status=Skipped with Error built as rich_text segments: "Duplicate of "
    + <original Name as hyperlink to original Notion page> + " — " +
    <canonical_url as hyperlink>. Queue.db is not polluted with a second
    row, and the normal write_triaged (status flip to Ready/Fetching) does
    not fire. Skipped (not Failed) so the Notion view separates intentional
    dedup skips from real errors."""
    resources, notion = _resources(tmp_path)
    notion.get_page_name.return_value = "Original Title"
    _seed_existing_triaged(
        resources["triage_store"],
        page_id="p-original",
        canonical_url="https://example.com/post",
    )
    result = _materialize(
        partition_key="p-dup",
        resources=resources,
        url="https://example.com/post",
    )
    assert result.success
    notion.get_page_name.assert_called_once_with("p-original")
    # Notion is flagged Skipped, not Ready/Fetching/Failed.
    notion.update_status_skipped.assert_called_once()
    skipped_args = notion.update_status_skipped.call_args
    assert skipped_args.args[0] == "p-dup"
    segments = skipped_args.args[1]
    # Segment shape: (text, link_url | None)
    assert segments[0] == ("Duplicate of ", None)
    assert segments[1] == ("Original Title", "https://www.notion.so/poriginal")
    assert segments[2] == (" — ", None)
    assert segments[3] == ("https://example.com/post", "https://example.com/post")
    notion.update_status_failed.assert_not_called()
    notion.write_triaged.assert_not_called()
    # Queue.db is NOT polluted with a row for p-dup.
    from domains.queue_store import sources as queue_db

    assert (
        queue_db.get_row(db_path=Path(resources["triage_store"].db_path), notion_page_id="p-dup")
        is None
    )
    # Materialization metadata flags the outcome for observability.
    metadata = _get_metadata(result)
    assert metadata["outcome"].text == "duplicate"
    assert metadata["duplicate_of"].text == "p-original"
    assert metadata["duplicate_of_name"].text == "Original Title"
    assert metadata["status_after"].text == "Skipped"


def test_triaged_duplicate_falls_back_to_untitled_when_no_name(tmp_path: Path):
    """If the original Notion page has no Name set, the link text falls
    back to "(untitled)" rather than a bare UUID."""
    resources, notion = _resources(tmp_path)
    notion.get_page_name.return_value = None
    _seed_existing_triaged(
        resources["triage_store"],
        page_id="p-original",
        canonical_url="https://example.com/post",
    )
    result = _materialize(
        partition_key="p-dup",
        resources=resources,
        url="https://example.com/post",
    )
    assert result.success
    segments = notion.update_status_skipped.call_args.args[1]
    assert segments[1] == ("(untitled)", "https://www.notion.so/poriginal")


def test_triaged_idempotent_on_re_triage_same_page(tmp_path: Path):
    """Re-triaging the SAME notion_page_id (e.g. Re-Queued from Failed) must
    NOT flag itself as a duplicate. Existing row for self stays; write_triaged
    fires normally."""
    resources, notion = _resources(tmp_path)
    _seed_existing_triaged(
        resources["triage_store"],
        page_id="p-1",
        canonical_url="https://example.com/post",
    )
    result = _materialize(
        partition_key="p-1",
        resources=resources,
        url="https://example.com/post",
    )
    assert result.success
    notion.update_status_skipped.assert_not_called()
    notion.update_status_failed.assert_not_called()
    notion.write_triaged.assert_called_once()


def test_triaged_dedup_uses_canonical_not_raw_url(tmp_path: Path):
    """A new URL with extra tracking params that canonicalizes to an
    already-triaged canonical_url → flagged as duplicate."""
    resources, notion = _resources(tmp_path)
    notion.get_page_name.return_value = "Original Title"
    _seed_existing_triaged(
        resources["triage_store"],
        page_id="p-original",
        canonical_url="https://example.com/post",
    )
    result = _materialize(
        partition_key="p-dup",
        resources=resources,
        url="https://example.com/post?utm_source=twitter",
    )
    assert result.success
    notion.update_status_skipped.assert_called_once()
    notion.write_triaged.assert_not_called()


def test_triaged_passes_none_added_at_when_unset(tmp_path: Path):
    """No added_at_iso in run_config → None forwarded to write_triaged."""
    resources, notion = _resources(tmp_path)
    result = _materialize(
        partition_key="p-1",
        resources=resources,
        url="https://example.com/post",
    )
    assert result.success
    assert notion.write_triaged.call_args.kwargs["added_at_iso"] is None


# -------- podcast → YouTube substitution --------


def test_triaged_substitutes_podcast_url_to_youtube_on_match(tmp_path: Path):
    """When classify yields Podcast and the canonicalize lookup finds a
    YouTube equivalent, the canonical URL written to queue.db + Notion is
    the YouTube URL and content_type is reclassified to YouTube. The
    fetcher service downstream gets a free transcript instead of paying
    for Whisper."""
    resources, notion = _resources(tmp_path)
    podcast_url = "https://traffic.megaphone.fm/SUPERDATASCIENCEPTYLTD7992118381.mp3"
    youtube_url = "https://www.youtube.com/watch?v=vi6UILzThgo"
    with patch(
        "orchestrators.defs.triage_knowledge_queue.assets.maybe_redirect_podcast_to_youtube",
        return_value=youtube_url,
    ):
        result = _materialize(
            partition_key="p-pod",
            resources=resources,
            url=podcast_url,
            name="Super Data Science: ML & AI Podcast with Jon Krohn: 999: What's Left to Build",
        )

    assert result.success
    metadata = _get_metadata(result)
    assert metadata["content_type"].text == "YouTube"
    assert metadata["canonical_url"].url == youtube_url
    assert metadata["podcast_substituted_to"].url == youtube_url
    write_kwargs = notion.write_triaged.call_args.kwargs
    assert write_kwargs["content_type"] == "YouTube"
    assert write_kwargs["canonical_url"] == youtube_url


def test_triaged_keeps_podcast_classification_when_no_substitution(tmp_path: Path):
    """When no YouTube equivalent is found, the row stays Podcast with
    the original audio URL — fetcher service will handle it via the
    podcast handler (when wired) or fall through to article handler today."""
    resources, notion = _resources(tmp_path)
    podcast_url = "https://traffic.libsyn.com/unknown-show/episode.mp3"
    with patch(
        "orchestrators.defs.triage_knowledge_queue.assets.maybe_redirect_podcast_to_youtube",
        return_value=None,
    ):
        result = _materialize(
            partition_key="p-pod",
            resources=resources,
            url=podcast_url,
        )

    assert result.success
    metadata = _get_metadata(result)
    assert metadata["content_type"].text == "Podcast"
    assert metadata["canonical_url"].url == podcast_url
    assert "podcast_substituted_to" not in metadata
    write_kwargs = notion.write_triaged.call_args.kwargs
    assert write_kwargs["content_type"] == "Podcast"
    assert write_kwargs["canonical_url"] == podcast_url


def test_triaged_skips_substitution_for_non_podcast_url(tmp_path: Path):
    """maybe_redirect_podcast_to_youtube is NOT called for non-podcast
    URLs — guards against accidental lookup latency on every queue item."""
    resources, _ = _resources(tmp_path)
    with patch(
        "orchestrators.defs.triage_knowledge_queue.assets.maybe_redirect_podcast_to_youtube"
    ) as fake_lookup:
        result = _materialize(
            partition_key="p-art",
            resources=resources,
            url="https://blog.example.com/post",
        )

    assert result.success
    fake_lookup.assert_not_called()


# -------- content_shape --------


def _seed_enrichment(store: QueueStoreResource, *, page_id: str, url: str, payload: str) -> None:
    """Pre-populate queue_items.enrichment_json as the `enriched` sibling
    asset would. Lets the triaged-asset tests cover the cross-asset wiring
    without orchestrating a full two-asset materialization."""
    from domains.queue_store import sources as queue_db

    queue_db.create_schema(db_path=Path(store.db_path))
    queue_db.upsert_enriched(
        db_path=Path(store.db_path),
        notion_page_id=page_id,
        url=url,
        enrichment_json=payload,
    )


def test_triaged_classifies_content_shape_from_enrichment_youtube_channel(tmp_path: Path):
    """YouTube row with `AI Engineer` channel enriched → triaged writes
    `content_shape="conference_talk"` to queue.db."""
    resources, _ = _resources(tmp_path)
    _seed_enrichment(
        resources["triage_store"],
        page_id="p-1",
        url="https://www.youtube.com/watch?v=abc",
        payload='{"youtube":{"channel":"AI Engineer","title":"A talk"}}',
    )
    result = _materialize(
        partition_key="p-1",
        resources=resources,
        url="https://www.youtube.com/watch?v=abc",
    )
    assert result.success
    metadata = _get_metadata(result)
    assert metadata["content_shape"].text == "conference_talk"
    from domains.queue_store import sources as queue_db

    row = queue_db.get_row(db_path=Path(resources["triage_store"].db_path), notion_page_id="p-1")
    assert row["content_shape"] == "conference_talk"


def test_triaged_classifies_unknown_when_no_enrichment_row(tmp_path: Path):
    """No prior `enriched` materialization → from_json(None) → empty signals
    → unknown shape. Asset must not crash and must write `unknown` to
    queue.db (extractor will fall back to generic prompt for unknown)."""
    resources, _ = _resources(tmp_path)
    result = _materialize(
        partition_key="p-1",
        resources=resources,
        url="https://random.example.com/post",
    )
    assert result.success
    metadata = _get_metadata(result)
    assert metadata["content_shape"].text == "unknown"
    from domains.queue_store import sources as queue_db

    row = queue_db.get_row(db_path=Path(resources["triage_store"].db_path), notion_page_id="p-1")
    assert row["content_shape"] == "unknown"


def test_triaged_classifies_research_summary_for_arxiv_without_enrichment(tmp_path: Path):
    """arXiv host trumps enrichment — even an empty `enriched` row still
    classifies as research_summary."""
    resources, _ = _resources(tmp_path)
    _seed_enrichment(
        resources["triage_store"],
        page_id="p-1",
        url="https://arxiv.org/abs/2105.04663",
        payload="{}",
    )
    result = _materialize(
        partition_key="p-1",
        resources=resources,
        url="https://arxiv.org/abs/2105.04663",
    )
    assert result.success
    metadata = _get_metadata(result)
    assert metadata["content_shape"].text == "research_summary"


def test_triaged_classifies_opinion_essay_for_substack_host(tmp_path: Path):
    """Article host rule fires when YouTube channel match doesn't."""
    resources, _ = _resources(tmp_path)
    _seed_enrichment(
        resources["triage_store"],
        page_id="p-1",
        url="https://ontologist.substack.com/p/essay",
        payload='{"article":{"redirected_url":"https://ontologist.substack.com/p/essay","title":"x","description":"y"}}',
    )
    result = _materialize(
        partition_key="p-1",
        resources=resources,
        url="https://ontologist.substack.com/p/essay",
    )
    assert result.success
    metadata = _get_metadata(result)
    assert metadata["content_shape"].text == "opinion_essay"


def test_triaged_respects_user_set_content_shape(tmp_path: Path):
    """User-set Content Shape on the Notion row wins over the classifier.
    The asset passes the override straight through and tags
    content_shape_source = "notion" for observability."""
    resources, notion = _resources(tmp_path)
    instance = _instance_with_partition("p-1")
    result = dg.materialize(
        [triaged],
        partition_key="p-1",
        resources=resources,
        instance=instance,
        tags={"notion_page_id": "p-1"},
        run_config={
            "ops": {
                "triage_knowledge_queue__triaged": {
                    "config": {
                        "url": "https://random.example.com/post",
                        "content_shape": "tutorial",
                    }
                }
            }
        },
    )
    assert result.success
    metadata = _get_metadata(result)
    assert metadata["content_shape"].text == "tutorial"
    assert metadata["content_shape_source"].text == "notion"
    assert notion.write_triaged.call_args.kwargs["content_shape"] == "tutorial"


def test_triaged_falls_back_to_classifier_on_typo_content_shape(tmp_path: Path):
    """Typo'd Content Shape (not in ALL_CONTENT_SHAPES) → rules classifier
    fires. Source = "classified"."""
    resources, _ = _resources(tmp_path)
    instance = _instance_with_partition("p-1")
    result = dg.materialize(
        [triaged],
        partition_key="p-1",
        resources=resources,
        instance=instance,
        tags={"notion_page_id": "p-1"},
        run_config={
            "ops": {
                "triage_knowledge_queue__triaged": {
                    "config": {
                        "url": "https://random.example.com/post",
                        "content_shape": "conferenc_talk",  # typo
                    }
                }
            }
        },
    )
    assert result.success
    metadata = _get_metadata(result)
    assert metadata["content_shape"].text == "unknown"
    assert metadata["content_shape_source"].text == "classified"


def test_triaged_survives_malformed_enrichment_json(tmp_path: Path):
    """Defensive: a malformed enrichment_json (e.g. partial write) falls
    through to empty signals → unknown shape; asset still succeeds."""
    resources, _ = _resources(tmp_path)
    _seed_enrichment(
        resources["triage_store"],
        page_id="p-1",
        url="https://example.com/post",
        payload="not json",
    )
    result = _materialize(
        partition_key="p-1",
        resources=resources,
        url="https://example.com/post",
    )
    assert result.success
    assert _get_metadata(result)["content_shape"].text == "unknown"
