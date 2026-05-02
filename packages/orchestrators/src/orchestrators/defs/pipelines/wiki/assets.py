# Wiki assets — synthesis (dynamic-partitioned), pending discovery, index regen.

import logging

import dagster as dg
import psycopg
from domains.wiki.sources import RawStoreSource
from domains.wiki.state import get_all_pages, get_processed_ids
from workflows.wiki_synthesis.runner import invoke_wiki_synthesis

from .resources import WikiResource

logger = logging.getLogger(__name__)

# One dynamic partition per item_id. wiki_pending discovers and registers new
# partitions on each run; wiki_synthesized then materializes one partition per
# article (allowing Dagster's executor to fan out as many as concurrency permits).
items_partitions = dg.DynamicPartitionsDefinition(name="wiki_items")


@dg.asset(
    group_name="wiki",
    compute_kind="postgres",
    description="Discover new raw_store items and register them as wiki_items partitions",
)
def wiki_pending(
    context: dg.AssetExecutionContext,
    wiki: WikiResource,
) -> dg.MaterializeResult:
    """Find raw_store items that don't yet have a wiki.processed row and add
    them as new wiki_items partitions for wiki_synthesized to materialize.

    Idempotent — re-runs add only the items that aren't already partitions
    and aren't already processed."""
    db_url = wiki.get_database_url()
    source = RawStoreSource(wiki.get_raw_store_path())

    all_items = source.get_items()
    with psycopg.connect(db_url) as conn:
        done_ids = get_processed_ids(conn, status="ok")
        skipped_ids = get_processed_ids(conn, status="skipped")
    handled = done_ids | skipped_ids

    pending_ids = [item.item_id for item in all_items if item.item_id not in handled]

    # Cost guardrail: if max_articles is set, only add that many new partitions
    # per run. Subsequent runs pick up the next batch.
    if wiki.max_articles > 0:
        pending_ids = pending_ids[: wiki.max_articles]

    existing_partitions = set(context.instance.get_dynamic_partitions(items_partitions.name))
    to_add = [pid for pid in pending_ids if pid not in existing_partitions]
    if to_add:
        context.instance.add_dynamic_partitions(items_partitions.name, to_add)

    context.log.info(
        "Wiki pending: %d total, %d done, %d failed-or-other, %d added as partitions",
        len(all_items),
        len(done_ids),
        len(handled) - len(done_ids),
        len(to_add),
    )
    return dg.MaterializeResult(
        metadata={
            "total_raw_items": dg.MetadataValue.int(len(all_items)),
            "done": dg.MetadataValue.int(len(done_ids)),
            "pending_added": dg.MetadataValue.int(len(to_add)),
            "existing_partitions": dg.MetadataValue.int(len(existing_partitions)),
        }
    )


@dg.asset(
    group_name="wiki",
    compute_kind="llm",
    partitions_def=items_partitions,
    description="Run the wiki_synthesis LangGraph workflow for one item_id",
)
def wiki_synthesized(
    context: dg.AssetExecutionContext,
    wiki: WikiResource,
) -> dg.MaterializeResult:
    """Per-partition: load the IngestItem from raw_store and invoke the
    wiki_synthesis workflow. The runner handles checkpointer setup,
    Langfuse tracing, and resume-vs-fresh detection.

    A retry of this asset on the same partition_key auto-resumes from
    the LangGraph checkpoint (see runner.py auto-resume heuristic) — no
    LLM re-calls if the prior failure was in commit."""
    item_id = context.partition_key
    db_url = wiki.get_database_url()

    item = RawStoreSource(wiki.get_raw_store_path()).get_item(item_id)
    if item is None:
        raise dg.Failure(
            description=f"raw_store has no item with content_id={item_id!r}",
            metadata={"item_id": dg.MetadataValue.text(item_id)},
        )

    invoke_wiki_synthesis(item, db_url=db_url, wiki_dir=wiki.get_wiki_dir())

    # Re-read processed status to surface the result in Dagster metadata.
    with psycopg.connect(db_url) as conn:
        row = conn.execute(
            "SELECT status, error FROM wiki.processed " "WHERE item_id = %s AND source_type = %s",
            (item.item_id, item.source_type),
        ).fetchone()

    status = row[0] if row else "unknown"
    error = row[1] if row else None
    return dg.MaterializeResult(
        metadata={
            "item_id": dg.MetadataValue.text(item_id),
            "source_type": dg.MetadataValue.text(item.source_type),
            "status": dg.MetadataValue.text(status),
            **({"error": dg.MetadataValue.text(error)} if error else {}),
        }
    )


@dg.asset(
    group_name="wiki",
    compute_kind="python",
    deps=[wiki_synthesized],
    description="Regenerate wiki index.md from current pages in Postgres",
)
def wiki_index_updated(
    context: dg.AssetExecutionContext,
    wiki: WikiResource,
) -> dg.MaterializeResult:
    """Read every wiki.pages row from Postgres and write a flat index.md
    grouped by page_type. Replaces the SQLite-backed version that read
    from WikiStateDB."""
    db_url = wiki.get_database_url()
    with psycopg.connect(db_url) as conn:
        pages = get_all_pages(conn)

    wiki_dir = wiki.get_wiki_dir()
    wiki_dir.mkdir(parents=True, exist_ok=True)

    lines = ["# Wiki Index", "", f"Total pages: {len(pages)}", ""]
    for page_type in ["concept", "tool", "trend"]:
        typed = [p for p in pages if p.page_type == page_type]
        if typed:
            lines.append(f"## {page_type.title()}s")
            lines.append("")
            for p in sorted(typed, key=lambda x: x.entity_id):
                lines.append(f"- [{p.entity_id}]({p.file_path})")
            lines.append("")

    index_path = wiki_dir / "index.md"
    index_path.write_text("\n".join(lines), encoding="utf-8")

    context.log.info("Wiki index updated: %d pages", len(pages))
    return dg.MaterializeResult(
        metadata={
            "page_count": dg.MetadataValue.int(len(pages)),
            "index_path": dg.MetadataValue.path(str(index_path)),
        }
    )
