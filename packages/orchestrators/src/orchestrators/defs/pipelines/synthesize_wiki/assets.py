# Wiki synthesis pipeline. See README.md for the DAG diagram and runbook.

import dagster as dg
import psycopg
from domains.wiki.sources import RawStoreSource
from domains.wiki.state import get_all_pages, get_processed_ids
from workflows.wiki_synthesis.runner import invoke_wiki_synthesis

from orchestrators.config import SYNTHESIZE_WIKI_DAG_VERSION

from .def_config import (
    PIPELINE_TAG,
    SOURCE_RAW_STORE,
    WIKI_ITEMS_PARTITIONS_NAME,
    item_partitions_def,
)
from .resources import WikiResource


@dg.asset(
    key=["wiki", "pending_contents"],
    group_name="wiki",
    compute_kind="sqlite",
    code_version=SYNTHESIZE_WIKI_DAG_VERSION,
    deps=[dg.AssetDep("raw_store")],
    op_tags={"dagster/concurrency_key": PIPELINE_TAG},
    description=(
        "Discover raw_store contents not yet in wiki.processed and register "
        "them as wiki_items dynamic partitions for downstream synthesis. "
        "Source-specific — sibling assets will land for notes and sessions."
    ),
)
def discover_pending_contents(
    context: dg.AssetExecutionContext,
    wiki: WikiResource,
) -> dg.MaterializeResult:
    all_ids = RawStoreSource(wiki.get_raw_store_path()).get_item_ids()

    if len(all_ids) >= 10_000:
        raise dg.Failure(
            description=(
                f"raw_store has {len(all_ids)} items — full-scan discovery "
                f"is no longer the right shape above 10k. Migrate to "
                f"sensor-driven discovery (Phase E) before re-running. "
                f"See ai-plannings/2026-05-07_phase-e-sensor-driven-discovery.md."
            ),
            metadata={"total_raw_items": dg.MetadataValue.int(len(all_ids))},
        )

    with psycopg.connect(wiki.database_url) as conn:
        done_ids = get_processed_ids(conn, status="ok")
        skipped_ids = get_processed_ids(conn, status="skipped")
    handled = done_ids | skipped_ids

    pending_ids = [cid for cid in all_ids if cid not in handled]
    if wiki.max_per_discovery > 0:
        pending_ids = pending_ids[: wiki.max_per_discovery]

    # Partition keys are source-prefixed so wiki_items can hold multiple
    # sources without id collisions. The discoverer owns the prefix; the
    # source layer keeps emitting raw IDs.
    pending_keys = [f"{SOURCE_RAW_STORE}:{cid}" for cid in pending_ids]
    existing = set(context.instance.get_dynamic_partitions(WIKI_ITEMS_PARTITIONS_NAME))
    to_add = [k for k in pending_keys if k not in existing]
    if to_add:
        context.instance.add_dynamic_partitions(WIKI_ITEMS_PARTITIONS_NAME, to_add)

    summary = (
        f"**Pending contents** — {len(all_ids)} raw items, "
        f"{len(done_ids)} done, {len(handled) - len(done_ids)} skipped/failed, "
        f"**{len(to_add)} new partitions registered** "
        f"(cap: {wiki.max_per_discovery or 'none'})"
    )
    return dg.MaterializeResult(
        metadata={
            "summary": dg.MetadataValue.md(summary),
            "total_raw_items": dg.MetadataValue.int(len(all_ids)),
            "done": dg.MetadataValue.int(len(done_ids)),
            "pending_added": dg.MetadataValue.int(len(to_add)),
            "existing_partitions": dg.MetadataValue.int(len(existing)),
        }
    )


@dg.asset(
    key=["wiki", "synthesized"],
    group_name="wiki",
    compute_kind="openai",
    code_version=SYNTHESIZE_WIKI_DAG_VERSION,
    partitions_def=item_partitions_def,
    deps=[dg.AssetDep(["wiki", "pending_contents"])],
    op_tags={"dagster/concurrency_key": PIPELINE_TAG},
    description=(
        "Run the wiki_synthesis LangGraph workflow for one IngestItem. "
        "Source-agnostic — handles whatever's in the wiki_items partition "
        "set. Per-partition retry auto-resumes from the LangGraph checkpoint."
    ),
)
def synthesize_item(
    context: dg.AssetExecutionContext,
    wiki: WikiResource,
) -> dg.MaterializeResult:
    # partition_key shape: "<source>:<raw_id>" (see discover_pending_*).
    source_type, raw_id = context.partition_key.split(":", 1)
    if source_type == SOURCE_RAW_STORE:
        item = RawStoreSource(wiki.get_raw_store_path()).get_item(raw_id)
    else:
        raise dg.Failure(
            description=(
                f"Unknown source_type {source_type!r} in partition_key "
                f"{context.partition_key!r}. Only {SOURCE_RAW_STORE!r} is "
                f"wired today; notes and sessions land in a follow-up PR."
            ),
            metadata={"partition_key": dg.MetadataValue.text(context.partition_key)},
        )
    if item is None:
        raise dg.Failure(
            description=f"{source_type} has no item with id={raw_id!r}",
            metadata={"item_id": dg.MetadataValue.text(raw_id)},
        )

    invoke_wiki_synthesis(item, db_url=wiki.database_url, wiki_dir=wiki.get_wiki_dir())

    # Re-read the processed row so the asset surfaces the workflow's
    # outcome — status='error' is a *successful* asset run, the workflow
    # caught the failure and committed an error marker.
    with psycopg.connect(wiki.database_url) as conn:
        row = conn.execute(
            "SELECT status, error FROM wiki.processed " "WHERE item_id = %s AND source_type = %s",
            (item.item_id, item.source_type),
        ).fetchone()

    status = row[0] if row else "unknown"
    error = row[1] if row else None
    metadata: dict[str, dg.MetadataValue] = {
        "item_id": dg.MetadataValue.text(raw_id),
        "source_type": dg.MetadataValue.text(item.source_type),
    }
    if status == "error" and error:
        metadata["summary"] = dg.MetadataValue.md(f"**workflow recorded error** — {error}")
        metadata["error"] = dg.MetadataValue.text(error)
    elif status not in {"ok", "error"}:
        metadata["status"] = dg.MetadataValue.text(status)
    return dg.MaterializeResult(metadata=metadata)


@dg.asset(
    key=["wiki", "index"],
    group_name="wiki",
    compute_kind="file",
    code_version=SYNTHESIZE_WIKI_DAG_VERSION,
    op_tags={"dagster/concurrency_key": PIPELINE_TAG},
    description=(
        "Regenerate data/wiki/index.md (table of contents) from "
        "wiki.pages. NOT declared as deps=[wiki/synthesized] — see "
        "README on AllPartitionMapping."
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


all_assets = [discover_pending_contents, synthesize_item, regenerate_toc]
