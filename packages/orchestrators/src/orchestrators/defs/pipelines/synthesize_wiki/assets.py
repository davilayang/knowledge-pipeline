# Wiki synthesis pipeline. See README.md for the DAG diagram and runbook.

from concurrent.futures import ThreadPoolExecutor, as_completed

import dagster as dg
import psycopg
from domains.wiki.sources import IngestItem, RawStoreSource
from domains.wiki.state import get_all_pages, get_processed_ids
from workflows.wiki_synthesis.runner import invoke_wiki_synthesis

from orchestrators.config import SYNTHESIZE_WIKI_DAG_VERSION

from .def_config import (
    PIPELINE_TAG,
    SOURCE_RAW_STORE,
    SYNTHESIS_CONCURRENCY,
    wiki_daily_partition_def,
)
from .resources import WikiResource


class SynthesizeWikiConfig(dg.Config):
    """Per-run inputs supplied by the schedule (or Launchpad)."""

    item_ids: list[str]
    source_type: str = SOURCE_RAW_STORE


@dg.asset(
    key=["wiki", "synthesized"],
    group_name="wiki",
    compute_kind="openai",
    code_version=SYNTHESIZE_WIKI_DAG_VERSION,
    partitions_def=wiki_daily_partition_def,
    deps=[dg.AssetDep("raw_store")],
    op_tags={"dagster/concurrency_key": PIPELINE_TAG},
    description=(
        "Run the wiki_synthesis LangGraph workflow for each pending item "
        "supplied via run_config. The schedule discovers raw_store ∖ "
        "wiki.processed and passes the slice as item_ids; the asset fans "
        "out internally with a ThreadPoolExecutor. Per-item failures are "
        "recorded in wiki.processed (status='error') without aborting the "
        "batch; only run-level (auth/infra) errors fail the Dagster run."
    ),
)
def synthesized(
    context: dg.AssetExecutionContext,
    config: SynthesizeWikiConfig,
    wiki: WikiResource,
) -> dg.MaterializeResult:
    if config.source_type != SOURCE_RAW_STORE:
        raise dg.Failure(
            description=(
                f"source_type={config.source_type!r} is not wired yet; "
                f"only {SOURCE_RAW_STORE!r} is supported."
            ),
        )

    if not config.item_ids:
        return dg.MaterializeResult(
            metadata={"summary": dg.MetadataValue.md("_no pending items this tick_")}
        )

    db_url = wiki.database_url
    # Re-filter against wiki.processed so retries don't re-pay for items that
    # already committed in a prior attempt. The schedule does this once at
    # tick time; we redo it here because run_config is replayed verbatim and
    # invoke_wiki_synthesis on a successfully-ended thread is a fresh run,
    # not a no-op.
    with psycopg.connect(db_url) as conn:
        handled = get_processed_ids(conn, status="ok") | get_processed_ids(conn, status="skipped")
    pending_ids = [iid for iid in config.item_ids if iid not in handled]
    already = len(config.item_ids) - len(pending_ids)
    if not pending_ids:
        return dg.MaterializeResult(
            metadata={
                "summary": dg.MetadataValue.md(f"_all {already} items already processed_"),
                "item_count": dg.MetadataValue.int(0),
                "skipped_already_processed": dg.MetadataValue.int(already),
            }
        )

    source = RawStoreSource(wiki.get_raw_store_path())
    items: list[IngestItem] = []
    missing: list[str] = []
    for raw_id in pending_ids:
        item = source.get_item(raw_id)
        if item is None:
            missing.append(raw_id)
        else:
            items.append(item)
    if missing:
        raise dg.Failure(
            description=f"raw_store missing {len(missing)} item(s) supplied via run_config",
            metadata={"missing": dg.MetadataValue.json(missing[:50])},
        )
    wiki_dir = wiki.get_wiki_dir()
    errors: list[tuple[str, str]] = []
    with ThreadPoolExecutor(max_workers=SYNTHESIS_CONCURRENCY) as pool:
        futures = {
            pool.submit(invoke_wiki_synthesis, item, db_url=db_url, wiki_dir=wiki_dir): item
            for item in items
        }
        for fut in as_completed(futures):
            item = futures[fut]
            try:
                fut.result()
            except Exception as e:
                context.log.exception("wiki synthesis raised for %s", item.item_id)
                errors.append((item.item_id, repr(e)))

    with psycopg.connect(db_url) as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*) FROM wiki.processed "
            "WHERE source_type = %s AND item_id = ANY(%s) "
            "GROUP BY status",
            (config.source_type, [i.item_id for i in items]),
        ).fetchall()
    by_status = {status: count for status, count in rows}

    summary_parts = [f"**{len(items)} items**"]
    if by_status:
        summary_parts.append(", ".join(f"{c} {s}" for s, c in sorted(by_status.items())))
    if errors:
        summary_parts.append(f"{len(errors)} raised")
    if already:
        summary_parts.append(f"{already} already processed (skipped)")
    metadata: dict[str, dg.MetadataValue] = {
        "summary": dg.MetadataValue.md(" — ".join(summary_parts)),
        "item_count": dg.MetadataValue.int(len(items)),
        "by_status": dg.MetadataValue.json(by_status),
    }
    if already:
        metadata["skipped_already_processed"] = dg.MetadataValue.int(already)
    if errors:
        raise dg.Failure(
            description=f"{len(errors)} item(s) raised out of the workflow",
            metadata={**metadata, "errors": dg.MetadataValue.json(errors)},
        )
    return dg.MaterializeResult(metadata=metadata)


@dg.asset(
    key=["wiki", "index"],
    group_name="wiki",
    compute_kind="file",
    code_version=SYNTHESIZE_WIKI_DAG_VERSION,
    partitions_def=wiki_daily_partition_def,
    deps=[dg.AssetDep(["wiki", "synthesized"])],
    op_tags={"dagster/concurrency_key": PIPELINE_TAG},
    description=(
        "Regenerate data/wiki/index.md (table of contents) from wiki.pages "
        "after this tick's synthesis lands. Reads the full pages table — "
        "the partition gates ordering, not scope."
    ),
)
def regenerate_toc(
    context: dg.AssetExecutionContext,
    wiki: WikiResource,
) -> dg.MaterializeResult:
    with psycopg.connect(wiki.database_url) as conn:
        pages = get_all_pages(conn)

    wiki_dir = wiki.get_wiki_dir()
    wiki_dir.mkdir(parents=True, exist_ok=True)

    lines = ["# Wiki Index", "", f"Total pages: {len(pages)}", ""]
    for page_type in ("concept", "tool", "trend"):
        typed = [p for p in pages if p.page_type == page_type]
        if typed:
            lines.append(f"## {page_type.title()}s")
            lines.append("")
            for p in sorted(typed, key=lambda x: x.entity_id):
                lines.append(f"- [{p.entity_id}]({p.file_path})")
            lines.append("")

    index_path = wiki_dir / "index.md"
    index_path.write_text("\n".join(lines), encoding="utf-8")

    summary = (
        f"**Index regenerated** — {len(pages)} pages "
        f"({sum(1 for p in pages if p.page_type == 'concept')} concept, "
        f"{sum(1 for p in pages if p.page_type == 'tool')} tool, "
        f"{sum(1 for p in pages if p.page_type == 'trend')} trend)"
    )
    return dg.MaterializeResult(
        metadata={
            "summary": dg.MetadataValue.md(summary),
            "page_count": dg.MetadataValue.int(len(pages)),
            "index_path": dg.MetadataValue.path(str(index_path)),
        }
    )


all_assets = [synthesized, regenerate_toc]
