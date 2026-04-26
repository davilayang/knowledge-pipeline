# Wiki assets — synthesis, pending snapshot, and index regeneration.

import logging

import dagster as dg

from knowledge_pipeline.lib.wiki.ingest import ingest_article
from knowledge_pipeline.lib.wiki.sources import RawStoreSource
from knowledge_pipeline.lib.wiki.state import WikiStateDB

from .resources import WikiResource

logger = logging.getLogger(__name__)


@dg.asset(
    group_name="wiki",
    compute_kind="llm",
    description="Synthesize wiki pages from pending raw articles via LLM",
)
def wiki_synthesized(
    context: dg.AssetExecutionContext,
    wiki: WikiResource,
) -> dg.MaterializeResult:
    """Process all pending articles through entity extraction + page synthesis."""
    state_db = WikiStateDB(wiki.get_state_db_path())
    try:
        done_ids = state_db.get_processed_ids("done")

        source = RawStoreSource()
        all_items = source.get_items()
        pending = [item for item in all_items if item.item_id not in done_ids]

        # Cost guardrail
        if wiki.max_articles > 0:
            pending = pending[: wiki.max_articles]

        context.log.info(
            "Wiki synthesis: %d pending (%d total, %d done)",
            len(pending),
            len(all_items),
            len(done_ids),
        )

        if not pending:
            return dg.MaterializeResult(
                metadata={
                    "processed": dg.MetadataValue.int(0),
                    "pages_touched": dg.MetadataValue.int(0),
                    "errors": dg.MetadataValue.int(0),
                }
            )

        all_pages: list[str] = []
        errors = 0

        for item in pending:
            try:
                pages_touched = ingest_article(
                    item,
                    wiki_dir=wiki.get_wiki_dir(),
                    aliases_path=wiki.get_aliases_path(),
                    state_db=state_db,
                )
                state_db.mark_processed(
                    item_id=item.item_id,
                    source_type=item.source_type,
                    status="done",
                    pages_touched=pages_touched,
                )
                all_pages.extend(pages_touched)
                context.log.info(
                    "Synthesized %s: %d pages (%s)",
                    item.item_id,
                    len(pages_touched),
                    ", ".join(pages_touched),
                )
            except Exception:
                logger.exception("Failed to ingest article %s", item.item_id)
                state_db.mark_processed(
                    item_id=item.item_id,
                    source_type=item.source_type,
                    status="failed",
                    error=str(item.item_id),
                )
                errors += 1

        return dg.MaterializeResult(
            metadata={
                "processed": dg.MetadataValue.int(len(pending)),
                "pages_touched": dg.MetadataValue.int(len(all_pages)),
                "errors": dg.MetadataValue.int(errors),
            }
        )
    finally:
        state_db.close()


@dg.asset(
    group_name="wiki",
    compute_kind="sqlite",
    description="Snapshot of pending articles not yet wiki-processed",
)
def wiki_pending(
    context: dg.AssetExecutionContext,
    wiki: WikiResource,
) -> dg.MaterializeResult:
    """Count pending items across all sources."""
    state_db = WikiStateDB(wiki.get_state_db_path())
    try:
        done_ids = state_db.get_processed_ids("done")
        failed_ids = state_db.get_processed_ids("failed")
        all_items = RawStoreSource().get_items()
        pending = [i for i in all_items if i.item_id not in done_ids]

        context.log.info(
            "Wiki status: %d pending, %d done, %d failed",
            len(pending),
            len(done_ids),
            len(failed_ids),
        )

        return dg.MaterializeResult(
            metadata={
                "pending": dg.MetadataValue.int(len(pending)),
                "done": dg.MetadataValue.int(len(done_ids)),
                "failed": dg.MetadataValue.int(len(failed_ids)),
                "total_sources": dg.MetadataValue.int(len(all_items)),
            }
        )
    finally:
        state_db.close()


@dg.asset(
    group_name="wiki",
    compute_kind="python",
    deps=[wiki_synthesized],
    description="Regenerate wiki index.md from current pages",
)
def wiki_index_updated(
    context: dg.AssetExecutionContext,
    wiki: WikiResource,
) -> dg.MaterializeResult:
    """Regenerate index.md listing all wiki pages."""
    state_db = WikiStateDB(wiki.get_state_db_path())
    try:
        pages = state_db.get_all_pages()

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
    finally:
        state_db.close()
