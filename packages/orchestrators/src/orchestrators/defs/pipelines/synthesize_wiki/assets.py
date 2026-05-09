# Wiki synthesis pipeline. See README.md for the DAG diagram and runbook.

from concurrent.futures import ThreadPoolExecutor, as_completed

import dagster as dg
import psycopg
from domains.wiki.sources import IngestItem, RawStoreSource
from domains.wiki.state import get_all_pages, get_processed_ids
from workflows.costs import PRICING_PER_1M, cost_usd
from workflows.llm import LLMCall
from workflows.wiki_synthesis.runner import invoke_wiki_synthesis

from orchestrators.config import SYNTHESIZE_WIKI_DAG_VERSION

from .def_config import (
    ALLOWED_CONTENT_ID_PREFIXES,
    MAX_PER_TICK_DEFAULT,
    PIPELINE_TAG,
    SOURCE_RAW_STORE,
    SYNTHESIS_CONCURRENCY,
    wiki_daily_partition_def,
)
from .resources import WikiResource


def _cost_metadata(calls: list[LLMCall]) -> dict[str, dg.MetadataValue]:
    """Aggregate per-call usage into Dagster MetadataValue entries."""
    total_in = sum(c.input_tokens for c in calls)
    total_out = sum(c.output_tokens for c in calls)
    # `sum(<gen>)` returns int 0 when the generator is empty; force float so
    # dg.MetadataValue.float() doesn't type-reject the no-calls case.
    total_usd = sum((cost_usd(c.model, c.input_tokens, c.output_tokens) for c in calls), 0.0)
    by_model = {
        m: {
            "calls": sum(1 for c in calls if c.model == m),
            "input_tokens": sum(c.input_tokens for c in calls if c.model == m),
            "output_tokens": sum(c.output_tokens for c in calls if c.model == m),
            "cost_usd": round(
                sum(
                    cost_usd(c.model, c.input_tokens, c.output_tokens)
                    for c in calls
                    if c.model == m
                ),
                4,
            ),
        }
        for m in {c.model for c in calls}
    }
    out: dict[str, dg.MetadataValue] = {
        "llm_calls": dg.MetadataValue.int(len(calls)),
        "input_tokens": dg.MetadataValue.int(total_in),
        "output_tokens": dg.MetadataValue.int(total_out),
        "cost_usd": dg.MetadataValue.float(round(total_usd, 4)),
        "cost_by_model": dg.MetadataValue.json(by_model),
    }
    unknown = sorted({c.model for c in calls if c.model not in PRICING_PER_1M})
    if unknown:
        out["unknown_pricing_models"] = dg.MetadataValue.json(unknown)
    return out


@dg.asset(
    key=["wiki", "pending"],
    group_name="wiki",
    kinds={"sqlite", "postgres"},
    code_version=SYNTHESIZE_WIKI_DAG_VERSION,
    partitions_def=wiki_daily_partition_def,
    deps=[dg.AssetDep(["snapshots", "raw_store"])],
    op_tags={"dagster/concurrency_key": PIPELINE_TAG},
    description=(
        "Discover raw_store items not yet in wiki.processed against the "
        "snapshots/raw_store partition with the same key (1:1 via the "
        "default IdentityPartitionMapping). Output is the capped work order "
        "consumed by wiki/synthesized; metadata exposes the pre-cap backlog "
        "so growth is visible per partition."
    ),
)
def pending(context: dg.AssetExecutionContext, wiki: WikiResource) -> dg.Output[list[str]]:
    snapshot_path = wiki.snapshot_path_for(context.partition_key)
    if not snapshot_path.exists():
        raise dg.Failure(
            description=(
                f"Snapshot missing: {snapshot_path}. backup_readings has "
                f"not materialised partition {context.partition_key}."
            ),
            metadata={"snapshot_path": dg.MetadataValue.path(str(snapshot_path))},
        )

    raw_ids = RawStoreSource(snapshot_path).get_item_ids()
    eligible = [r for r in raw_ids if r.startswith(ALLOWED_CONTENT_ID_PREFIXES)]
    excluded_by_source = len(raw_ids) - len(eligible)
    with psycopg.connect(wiki.database_url) as conn:
        handled = get_processed_ids(conn, status="ok") | get_processed_ids(conn, status="skipped")
    full = [r for r in eligible if r not in handled]
    queued = full[:MAX_PER_TICK_DEFAULT] if MAX_PER_TICK_DEFAULT > 0 else full
    return dg.Output(
        queued,
        metadata={
            "summary": dg.MetadataValue.md(
                f"**{len(queued)} queued** (backlog {len(full)}"
                + (", capped" if len(queued) < len(full) else "")
                + f"; {excluded_by_source} excluded by source allowlist)"
            ),
            "total_pending": dg.MetadataValue.int(len(full)),
            "queued": dg.MetadataValue.int(len(queued)),
            "capped": dg.MetadataValue.bool(len(queued) < len(full)),
            "excluded_by_source": dg.MetadataValue.int(excluded_by_source),
            "allowed_prefixes": dg.MetadataValue.json(list(ALLOWED_CONTENT_ID_PREFIXES)),
            "snapshot_path": dg.MetadataValue.path(str(snapshot_path)),
        },
    )


@dg.asset(
    key=["wiki", "synthesized"],
    group_name="wiki",
    kinds={"openai", "sqlite", "postgres"},
    code_version=SYNTHESIZE_WIKI_DAG_VERSION,
    partitions_def=wiki_daily_partition_def,
    ins={"pending": dg.AssetIn(["wiki", "pending"])},
    op_tags={"dagster/concurrency_key": PIPELINE_TAG},
    description=(
        "Run the wiki_synthesis LangGraph workflow for each item in the "
        "wiki/pending list. Re-filters against wiki.processed so retries "
        "don't re-pay for items committed in a prior attempt. Per-item "
        "failures land in wiki.processed (status='error') without aborting "
        "the batch; only run-level (auth/infra) errors fail the Dagster run."
    ),
)
def synthesized(
    context: dg.AssetExecutionContext,
    pending: list[str],
    wiki: WikiResource,
) -> dg.MaterializeResult:
    if not pending:
        return dg.MaterializeResult(
            metadata={"summary": dg.MetadataValue.md("_no pending items this tick_")}
        )

    snapshot_path = wiki.snapshot_path_for(context.partition_key)
    db_url = wiki.database_url
    # Re-filter against wiki.processed so retries don't re-pay for items that
    # already committed in a prior attempt. Dagster retry replays the
    # IO-manager-pickled `pending` list verbatim, and a successfully-ended
    # LangGraph thread re-runs from START on a fresh invoke.
    with psycopg.connect(db_url) as conn:
        handled = get_processed_ids(conn, status="ok") | get_processed_ids(conn, status="skipped")
    pending_ids = [iid for iid in pending if iid not in handled]
    already = len(pending) - len(pending_ids)
    if not pending_ids:
        return dg.MaterializeResult(
            metadata={
                "summary": dg.MetadataValue.md(f"_all {already} items already processed_"),
                "item_count": dg.MetadataValue.int(0),
                "skipped_already_processed": dg.MetadataValue.int(already),
            }
        )

    source = RawStoreSource(snapshot_path)
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
            description=f"raw_store missing {len(missing)} item(s) from wiki/pending",
            metadata={"missing": dg.MetadataValue.json(missing[:50])},
        )
    wiki_dir = wiki.get_wiki_dir()
    errors: list[tuple[str, str]] = []
    all_calls: list[LLMCall] = []
    with ThreadPoolExecutor(max_workers=SYNTHESIS_CONCURRENCY) as pool:
        futures = {
            pool.submit(invoke_wiki_synthesis, item, db_url=db_url, wiki_dir=wiki_dir): item
            for item in items
        }
        for fut in as_completed(futures):
            item = futures[fut]
            try:
                final_state = fut.result()
            except Exception as e:
                context.log.exception("wiki synthesis raised for %s", item.item_id)
                errors.append((item.item_id, repr(e)))
            else:
                all_calls.extend(final_state.get("llm_calls", []))

    with psycopg.connect(db_url) as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*) FROM wiki.processed "
            "WHERE source_type = %s AND item_id = ANY(%s) "
            "GROUP BY status",
            (SOURCE_RAW_STORE, [i.item_id for i in items]),
        ).fetchall()
    by_status = {status: count for status, count in rows}

    summary_parts = [f"**{len(items)} items**"]
    if by_status:
        summary_parts.append(", ".join(f"{c} {s}" for s, c in sorted(by_status.items())))
    if errors:
        summary_parts.append(f"{len(errors)} raised (cost may underreport)")
    if already:
        summary_parts.append(f"{already} already processed (skipped)")
    metadata: dict[str, dg.MetadataValue] = {
        "summary": dg.MetadataValue.md(" — ".join(summary_parts)),
        "item_count": dg.MetadataValue.int(len(items)),
        "by_status": dg.MetadataValue.json(by_status),
        "source_snapshot_path": dg.MetadataValue.path(str(snapshot_path)),
    }
    if already:
        metadata["skipped_already_processed"] = dg.MetadataValue.int(already)
    metadata.update(_cost_metadata(all_calls))
    # Per-item failures may have racked up LLM calls before raising; those
    # are inside the LangGraph thread state, not in the future's return.
    metadata["cost_complete"] = dg.MetadataValue.bool(not errors)
    if errors:
        raise dg.Failure(
            description=f"{len(errors)} item(s) raised out of the workflow",
            metadata={**metadata, "errors": dg.MetadataValue.json(errors)},
        )
    return dg.MaterializeResult(metadata=metadata)


@dg.asset(
    key=["wiki", "index"],
    group_name="wiki",
    kinds={"postgres", "file"},
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


all_assets = [pending, synthesized, regenerate_toc]
