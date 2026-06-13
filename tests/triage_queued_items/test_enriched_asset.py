"""Tests for the `enriched` Dagster asset.

The asset is pure-I/O: dispatches to `enrich_url`, writes the result as
`enrichment_json` on the queue_items row, and emits observability metadata.
The contract under test:

- Always succeeds — empty signals on per-source failure, never propagates.
- Re-materialisation overwrites enrichment_json (idempotent by page_id).
- MaterializeResult metadata names which sub-signals fetched and how many
  chars landed.
"""

from pathlib import Path
from unittest.mock import patch

import dagster as dg
from domains.queue_store import sources as queue_db
from orchestrators.defs.shared.queue_resources import QueueStoreResource
from orchestrators.defs.triage_queued_items.assets import enriched
from orchestrators.defs.triage_queued_items.def_config import queue_items_partition_def
from orchestrators.defs.triage_queued_items.enrich import (
    ArticleSignals,
    ArxivSignals,
    EnrichmentSignals,
    YoutubeSignals,
)


def _instance_with_partition(page_id: str) -> dg.DagsterInstance:
    instance = dg.DagsterInstance.ephemeral()
    instance.add_dynamic_partitions(queue_items_partition_def.name, [page_id])
    return instance


def _materialize(*, partition_key: str, url: str, store: QueueStoreResource):
    instance = _instance_with_partition(partition_key)
    return dg.materialize(
        [enriched],
        partition_key=partition_key,
        resources={"triage_store": store},
        instance=instance,
        tags={"notion_page_id": partition_key},
        run_config={"ops": {"triage_queued_items__enriched": {"config": {"url": url}}}},
    )


def _get_metadata(result) -> dict:
    mat_events = [e for e in result.all_events if e.event_type_value == "ASSET_MATERIALIZATION"]
    assert mat_events
    return mat_events[0].materialization.metadata


def _patch_enrich(signals: EnrichmentSignals):
    return patch(
        "orchestrators.defs.triage_queued_items.assets.enrich_url",
        return_value=signals,
    )


def test_enriched_writes_enrichment_json_to_queue_db(tmp_path: Path):
    store = QueueStoreResource(db_path=str(tmp_path / "q.db"))
    signals = EnrichmentSignals(youtube=YoutubeSignals(channel="AI Engineer", title="A talk"))
    with _patch_enrich(signals):
        result = _materialize(
            partition_key="p-1",
            url="https://www.youtube.com/watch?v=abc",
            store=store,
        )
    assert result.success
    row = queue_db.get_row(db_path=Path(store.db_path), notion_page_id="p-1")
    assert row is not None
    assert row["url"] == "https://www.youtube.com/watch?v=abc"
    assert row["enrichment_json"] == signals.to_json()


def test_enriched_idempotent_overwrites_on_re_materialization(tmp_path: Path):
    store = QueueStoreResource(db_path=str(tmp_path / "q.db"))
    first = EnrichmentSignals(youtube=YoutubeSignals(channel="First", title="t1"))
    second = EnrichmentSignals(youtube=YoutubeSignals(channel="Second", title="t2"))

    with _patch_enrich(first):
        r1 = _materialize(
            partition_key="p-1",
            url="https://www.youtube.com/watch?v=abc",
            store=store,
        )
    assert r1.success

    with _patch_enrich(second):
        r2 = _materialize(
            partition_key="p-1",
            url="https://www.youtube.com/watch?v=abc",
            store=store,
        )
    assert r2.success
    row = queue_db.get_row(db_path=Path(store.db_path), notion_page_id="p-1")
    assert row["enrichment_json"] == second.to_json()


def test_enriched_writes_empty_signals_on_enrichment_failure(tmp_path: Path):
    """`enrich_url` collapses to empty signals on HTTP error; the asset still
    succeeds and lands `{}` in enrichment_json."""
    store = QueueStoreResource(db_path=str(tmp_path / "q.db"))
    with _patch_enrich(EnrichmentSignals()):
        result = _materialize(
            partition_key="p-1",
            url="https://arxiv.org/abs/2105.04663",
            store=store,
        )
    assert result.success
    row = queue_db.get_row(db_path=Path(store.db_path), notion_page_id="p-1")
    assert row["enrichment_json"] == "{}"


def test_enriched_metadata_lists_signals_fetched(tmp_path: Path):
    store = QueueStoreResource(db_path=str(tmp_path / "q.db"))
    signals = EnrichmentSignals(arxiv=ArxivSignals(title="t", abstract="a", categories=("cs.LG",)))
    with _patch_enrich(signals):
        result = _materialize(
            partition_key="p-1",
            url="https://arxiv.org/abs/2105.04663",
            store=store,
        )
    md = _get_metadata(result)
    assert md["content_type"].text == "arXiv"
    assert md["signals_fetched"].text == "arxiv"
    assert md["enrichment_chars"].value == len(signals.to_json())


def test_enriched_metadata_marks_none_when_no_signals(tmp_path: Path):
    store = QueueStoreResource(db_path=str(tmp_path / "q.db"))
    with _patch_enrich(EnrichmentSignals()):
        result = _materialize(
            partition_key="p-1",
            url="https://news.ycombinator.com/item?id=1",
            store=store,
        )
    md = _get_metadata(result)
    assert md["signals_fetched"].text == "(none)"


def test_enriched_classifies_content_type_from_url(tmp_path: Path):
    """The asset re-runs `classify_content_type` to dispatch enrichment. The
    classified type lands in metadata so the operator can spot mis-routing."""
    store = QueueStoreResource(db_path=str(tmp_path / "q.db"))
    signals = EnrichmentSignals(
        article=ArticleSignals(redirected_url="https://blog.example.com/post")
    )
    with _patch_enrich(signals):
        result = _materialize(
            partition_key="p-1",
            url="https://blog.example.com/post",
            store=store,
        )
    md = _get_metadata(result)
    assert md["content_type"].text == "Article"
