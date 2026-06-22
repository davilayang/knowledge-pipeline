# Wiki synthesis pipeline. See README.md for the DAG diagram and runbook.

import json
import os
import time

import dagster as dg
from domains.raw_store.sources import RawStoreSource
from domains.types import IngestItem
from domains.wiki.identity import normalize_name
from domains.wiki.state import connection, get_all_pages, get_processed_ids
from workflows.costs import cost_usd, is_priced
from workflows.llm import LLMCall
from workflows.shared.observability import flush_langfuse
from workflows.wiki_synthesis.synthesize import extract_item, synthesize_extracted_item

from orchestrators.config import SYNTHESIZE_WIKI_DAG_VERSION

from .def_config import (
    ALLOWED_CONTENT_ID_PREFIXES,
    PIPELINE_TAG,
    SOURCE_RAW_STORE,
    WIKI_MAX_PER_TICK,
    wiki_daily_partition_def,
)
from .denylist import load_rejected_entities
from .resources import WikiPagesNotionResource, WikiResource


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
    unknown = sorted({c.model for c in calls if not is_priced(c.model)})
    if unknown:
        out["unknown_pricing_models"] = dg.MetadataValue.json(unknown)
    return out


@dg.asset(
    key=["wiki", "pending"],
    group_name="wiki",
    kinds={"sqlite"},
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

    source = RawStoreSource(snapshot_path)
    raw_ids = source.get_item_ids()
    fetched = set(source.get_item_ids(with_body=True))
    # Skip items the fetcher hasn't filled yet: an empty body extracts zero
    # entities but still marks the item `processed`, so it would never be
    # re-synthesised once the body lands.
    allowed = [r for r in raw_ids if r.startswith(ALLOWED_CONTENT_ID_PREFIXES)]
    eligible = [r for r in allowed if r in fetched]
    excluded_by_source = len(raw_ids) - len(allowed)
    excluded_unfetched = len(allowed) - len(eligible)
    with connection(wiki.get_db_path()) as conn:
        handled = get_processed_ids(conn, status="ok") | get_processed_ids(conn, status="skipped")
    full = [r for r in eligible if r not in handled]
    queued = full[:WIKI_MAX_PER_TICK] if WIKI_MAX_PER_TICK > 0 else full
    return dg.Output(
        queued,
        metadata={
            "summary": dg.MetadataValue.md(
                f"**{len(queued)} queued** (backlog {len(full)}"
                + (", capped" if len(queued) < len(full) else "")
                + f"; {excluded_by_source} excluded by source allowlist"
                + f"; {excluded_unfetched} unfetched)"
            ),
            "total_pending": dg.MetadataValue.int(len(full)),
            "queued": dg.MetadataValue.int(len(queued)),
            "capped": dg.MetadataValue.bool(len(queued) < len(full)),
            "excluded_by_source": dg.MetadataValue.int(excluded_by_source),
            "excluded_unfetched": dg.MetadataValue.int(excluded_unfetched),
            "allowed_prefixes": dg.MetadataValue.json(list(ALLOWED_CONTENT_ID_PREFIXES)),
            "snapshot_path": dg.MetadataValue.path(str(snapshot_path)),
            "item_ids": dg.MetadataValue.json(queued),
        },
    )


@dg.asset(
    key=["wiki", "extracted"],
    group_name="wiki",
    kinds={"openai", "sqlite"},
    code_version=SYNTHESIZE_WIKI_DAG_VERSION,
    partitions_def=wiki_daily_partition_def,
    ins={"pending": dg.AssetIn(["wiki", "pending"])},
    op_tags={"dagster/concurrency_key": PIPELINE_TAG},
    description=(
        "Run the entity-extraction LLM (call #1) for each item in wiki/pending, "
        "sequentially. Emits a per-item candidate map {item_id: {candidates, "
        "extract_error}} consumed by wiki/synthesized. Writes NO DB state — the "
        "candidates are UNRESOLVED names; minting/dedup happens snapshot-live in "
        "synthesis. Surfaces extraction cost separately from synthesis."
    ),
)
def extracted(
    context: dg.AssetExecutionContext,
    pending: list[str],
    wiki: WikiResource,
) -> dg.Output[dict]:
    if not pending:
        return dg.Output(
            {}, metadata={"summary": dg.MetadataValue.md("_no pending items this tick_")}
        )

    snapshot_path = wiki.snapshot_path_for(context.partition_key)
    db_path = wiki.get_db_path()
    source = RawStoreSource(snapshot_path)

    items = {raw_id: source.get_item(raw_id) for raw_id in pending}
    missing = [raw_id for raw_id, item in items.items() if item is None]
    if missing:
        raise dg.Failure(
            description=f"raw_store missing {len(missing)} item(s) from wiki/pending",
            metadata={"missing": dg.MetadataValue.json(missing[:50])},
        )

    payload: dict[str, dict] = {}
    all_calls: list[LLMCall] = []
    n_candidates = 0
    n_errors = 0
    for i, raw_id in enumerate(pending, 1):
        ext = extract_item(items[raw_id], db_path=db_path)
        all_calls.extend(ext.pop("llm_calls", []))
        payload[raw_id] = ext
        n_candidates += len(ext["candidates"])
        n_errors += 1 if ext["extract_error"] else 0
        context.log.info(
            "[%d/%d] extracted %s (%d candidates)", i, len(pending), raw_id, len(ext["candidates"])
        )

    flush_langfuse()

    metadata: dict[str, dg.MetadataValue] = {
        "summary": dg.MetadataValue.md(
            f"**{len(pending)} items** — {n_candidates} candidates"
            + (f", {n_errors} extraction errors" if n_errors else "")
        ),
        "item_count": dg.MetadataValue.int(len(pending)),
        "candidate_count": dg.MetadataValue.int(n_candidates),
        "extract_errors": dg.MetadataValue.int(n_errors),
        "source_snapshot_path": dg.MetadataValue.path(str(snapshot_path)),
    }
    metadata.update(_cost_metadata(all_calls))
    return dg.Output(payload, metadata=metadata)


@dg.asset(
    key=["wiki", "synthesized"],
    group_name="wiki",
    kinds={"openai", "sqlite"},
    code_version=SYNTHESIZE_WIKI_DAG_VERSION,
    partitions_def=wiki_daily_partition_def,
    ins={"extracted": dg.AssetIn(["wiki", "extracted"])},
    op_tags={"dagster/concurrency_key": PIPELINE_TAG},
    description=(
        "Resolve + synthesize (call #2) each item's extracted candidates, "
        "sequentially. Resolution mints/dedups against a LIVE entity index; the "
        "persist txn is idempotent (ON CONFLICT) so a retry re-processes already-"
        "committed items at the cost of duplicate LLM spend. Per-item failures "
        "land in processed_items (status='error') without aborting the batch; "
        "only run-level (auth/infra) errors fail the Dagster run."
    ),
)
def synthesized(
    context: dg.AssetExecutionContext,
    extracted: dict,
    wiki: WikiResource,
    wiki_pages_notion: WikiPagesNotionResource,
) -> dg.MaterializeResult:
    if not extracted:
        return dg.MaterializeResult(
            metadata={"summary": dg.MetadataValue.md("_no extracted items this tick_")}
        )

    snapshot_path = wiki.snapshot_path_for(context.partition_key)
    db_path = wiki.get_db_path()

    source = RawStoreSource(snapshot_path)
    items: list[IngestItem] = []
    missing: list[str] = []
    for raw_id in extracted:
        item = source.get_item(raw_id)
        if item is None:
            missing.append(raw_id)
        else:
            items.append(item)
    if missing:
        raise dg.Failure(
            description=f"raw_store missing {len(missing)} item(s) from wiki/extracted",
            metadata={"missing": dg.MetadataValue.json(missing[:50])},
        )
    wiki_dir = wiki.get_wiki_dir()
    # W2.5 denylist: curator-marked rejects from the Notion "Wiki Pages" DB,
    # fail-closed to a last-known-good snapshot so a Notion outage never
    # silently re-admits rejected entities.
    rejected = frozenset(
        load_rejected_entities(wiki_pages_notion, wiki_dir / "_index" / "rejected.json")
    )
    context.log.info("denylist: %d rejected name(s)", len(rejected))
    errors: list[tuple[str, str]] = []
    all_calls: list[LLMCall] = []
    for i, item in enumerate(items, 1):
        context.log.info("[%d/%d] synthesizing %s", i, len(items), item.item_id)
        started = time.monotonic()
        try:
            final_state = synthesize_extracted_item(
                item,
                extracted[item.item_id],
                db_path=db_path,
                wiki_dir=wiki_dir,
                rejected_entities=rejected,
            )
        except Exception as e:
            context.log.exception("wiki synthesis raised for %s", item.item_id)
            errors.append((item.item_id, repr(e)))
        else:
            all_calls.extend(final_state.get("llm_calls", []))
            context.log.info(
                "[%d/%d] %s done in %.1fs", i, len(items), item.item_id, time.monotonic() - started
            )

    # Flush buffered Langfuse traces before the run exits (v3 ships async).
    flush_langfuse()

    item_ids = [i.item_id for i in items]
    placeholders = ",".join("?" * len(item_ids))
    with connection(db_path) as conn:
        rows = conn.execute(
            f"SELECT status, COUNT(*) FROM processed_items "
            f"WHERE source_type = ? AND item_id IN ({placeholders}) "
            f"GROUP BY status",
            (SOURCE_RAW_STORE, *item_ids),
        ).fetchall()
    by_status = {status: count for status, count in rows}

    summary_parts = [f"**{len(items)} items**"]
    if by_status:
        summary_parts.append(", ".join(f"{c} {s}" for s, c in sorted(by_status.items())))
    if errors:
        summary_parts.append(f"{len(errors)} raised (cost may underreport)")
    metadata: dict[str, dg.MetadataValue] = {
        "summary": dg.MetadataValue.md(" — ".join(summary_parts)),
        "item_count": dg.MetadataValue.int(len(items)),
        "by_status": dg.MetadataValue.json(by_status),
        "source_snapshot_path": dg.MetadataValue.path(str(snapshot_path)),
    }
    metadata.update(_cost_metadata(all_calls))
    # cost_complete is True only when no item raised out of invoke_wiki_synthesis.
    # In-workflow caught failures (e.g. parse error after the LLM returned)
    # are accounted by the workflow's llm_calls reducer.
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
    kinds={"sqlite", "file"},
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
    with connection(wiki.get_db_path()) as conn:
        pages = get_all_pages(conn)

    wiki_dir = wiki.get_wiki_dir()
    wiki_dir.mkdir(parents=True, exist_ok=True)

    lines = ["# Wiki Index", "", f"Total pages: {len(pages)}", ""]
    for page_type in ("concept", "tool", "trend"):
        typed = [p for p in pages if p.page_type == page_type]
        if typed:
            lines.append(f"## {page_type.title()}s")
            lines.append("")
            # Label by canonical name (the surrogate entity_id is opaque); the
            # link target is the flat {slug}-{shortid}.md file.
            for p in sorted(typed, key=lambda x: x.canonical_name.lower()):
                lines.append(f"- [{p.canonical_name}]({p.file_path})")
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


@dg.asset(
    key=["wiki", "aliases_index"],
    group_name="wiki",
    kinds={"sqlite", "json"},
    code_version=SYNTHESIZE_WIKI_DAG_VERSION,
    partitions_def=wiki_daily_partition_def,
    deps=[dg.AssetDep(["wiki", "synthesized"])],
    op_tags={"dagster/concurrency_key": PIPELINE_TAG},
    description=(
        "Flat alias→entity_id map at data/wiki/_index/aliases.json for the "
        "consumer agent's O(1) entity resolution. Includes both wiki.aliases "
        "rows and each page title.lower() so case-insensitive lookups via "
        "canonical title resolve the same as via aliases. Atomic write + "
        "byte-equality skip — re-running on a partition with no DB changes "
        "leaves the file untouched."
    ),
)
def aliases_index(
    context: dg.AssetExecutionContext,
    wiki: WikiResource,
) -> dg.MaterializeResult:
    with connection(wiki.get_db_path()) as conn:
        alias_rows = conn.execute("SELECT alias, entity_id FROM aliases").fetchall()
        title_rows = conn.execute("SELECT entity_id, file_path FROM pages").fetchall()
        # canonical_name lives on entities now; join to pages so only entities
        # with a synthesised page contribute a canonical-name key.
        page_title_rows = conn.execute(
            """
            SELECT e.entity_id, e.canonical_name
            FROM entities e
            JOIN pages p ON p.entity_id = e.entity_id
            """
        ).fetchall()

    flat: dict[str, str] = {}

    def _set(key: str, entity_id: str) -> None:
        # Normalise with the SAME key the DB/resolver use (lower + trim +
        # collapse-ws), not a bare .lower() — otherwise "Chroma DB" and
        # "  Chroma   DB " slip past collision detection into two keys.
        norm = normalize_name(key)
        existing = flat.get(norm)
        if existing is not None and existing != entity_id:
            raise dg.Failure(
                description=(
                    f"Alias collision: '{norm}' maps to both '{existing}' " f"and '{entity_id}'."
                ),
                metadata={
                    "alias": dg.MetadataValue.text(norm),
                    "entity_a": dg.MetadataValue.text(existing),
                    "entity_b": dg.MetadataValue.text(entity_id),
                },
            )
        flat[norm] = entity_id

    # Self-map every page entity_id first so entities with zero alias rows
    # still resolve (consumer agent's get_entity_profile(entity_id) must
    # never silently miss because the producer forgot to write an alias).
    for entity_id, _ in title_rows:
        _set(entity_id, entity_id)
    for alias, entity_id in alias_rows:
        _set(alias, entity_id)
    for entity_id, canonical in page_title_rows:
        _set(canonical, entity_id)

    entities_total = len({entity_id for _, entity_id in alias_rows} | {r[0] for r in title_rows})

    serialized = json.dumps(flat, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
    payload = serialized.encode("utf-8")

    index_dir = wiki.get_wiki_dir() / "_index"
    index_dir.mkdir(parents=True, exist_ok=True)
    output_path = index_dir / "aliases.json"

    unchanged = output_path.exists() and output_path.read_bytes() == payload
    if not unchanged:
        tmp = output_path.with_suffix(".json.tmp")
        tmp.write_bytes(payload)
        os.replace(tmp, output_path)

    summary_md = f"**{len(flat)} aliases** across {entities_total} entities" + (
        " — unchanged" if unchanged else " — written"
    )
    return dg.MaterializeResult(
        metadata={
            "summary": dg.MetadataValue.md(summary_md),
            "aliases_total": dg.MetadataValue.int(len(flat)),
            "entities_total": dg.MetadataValue.int(entities_total),
            "unchanged": dg.MetadataValue.bool(unchanged),
            "output_path": dg.MetadataValue.path(str(output_path)),
        }
    )


all_assets = [pending, extracted, synthesized, regenerate_toc, aliases_index]
