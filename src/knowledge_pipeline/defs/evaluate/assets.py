# Evaluation assets: run curated queries against (collection x strategy) combos
# and produce a comparison report.

import json
import logging
import time

import dagster as dg
from dagster import AssetExecutionContext

from knowledge_pipeline.config import EVAL_RESULTS_DIR
from knowledge_pipeline.lib.eval import mrr, precision_at_k, recall_at_k
from knowledge_pipeline.lib.retrieval import build_strategy
from knowledge_pipeline.lib.vector_store import get_client, get_collection

from .queries import EVAL_QUERIES
from .registry import EVAL_COMBOS, parse_combo

logger = logging.getLogger(__name__)

combo_partitions = dg.StaticPartitionsDefinition(EVAL_COMBOS)


@dg.asset(
    group_name="evaluate",
    compute_kind="evaluation",
    partitions_def=combo_partitions,
    description="Run curated queries against a (collection, strategy) combo and persist metrics",
)
def eval_strategy_run(context: AssetExecutionContext) -> dg.MaterializeResult:
    """Evaluate a single (collection, strategy) combo on the curated query set."""
    partition_key = context.partition_key
    collection_name, strategy_spec = parse_combo(partition_key)

    if not EVAL_QUERIES:
        context.log.warning("No eval queries defined — populate defs/evaluate/queries.py")
        return dg.MaterializeResult(
            metadata={"status": dg.MetadataValue.text("no queries defined")}
        )

    k = 5
    client = get_client()

    # Check collection exists
    existing = {c.name for c in client.list_collections()}
    if collection_name not in existing:
        context.log.warning("Collection %s not found", collection_name)
        return dg.MaterializeResult(
            metadata={"status": dg.MetadataValue.text(f"collection {collection_name} not found")}
        )

    collection = get_collection(client=client, collection_name=collection_name)
    if collection.count() == 0:
        context.log.warning("Collection %s is empty", collection_name)
        return dg.MaterializeResult(
            metadata={"status": dg.MetadataValue.text(f"collection {collection_name} is empty")}
        )

    strategy = build_strategy(collection, strategy_spec)

    total_recall = 0.0
    total_precision = 0.0
    total_mrr = 0.0
    total_latency = 0.0
    evaluated = 0

    for eq in EVAL_QUERIES:
        start = time.perf_counter()
        results = strategy.retrieve(eq.query, n_results=k)
        latency_ms = (time.perf_counter() - start) * 1000

        # Extract unique content_ids preserving first-occurrence order
        retrieved_content_ids = _unique_content_ids(results)

        total_recall += recall_at_k(retrieved_content_ids, eq.expected_content_ids, k)
        total_precision += precision_at_k(retrieved_content_ids, eq.expected_content_ids, k)
        total_mrr += mrr(retrieved_content_ids, eq.expected_content_ids)
        total_latency += latency_ms
        evaluated += 1

    metrics = {}
    if evaluated > 0:
        metrics = {
            "recall@5": total_recall / evaluated,
            "precision@5": total_precision / evaluated,
            "mrr": total_mrr / evaluated,
            "avg_latency_ms": total_latency / evaluated,
        }

    # Persist results to JSON
    EVAL_RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    result_path = EVAL_RESULTS_DIR / f"{partition_key}.json"
    result_data = {
        "partition_key": partition_key,
        "collection": collection_name,
        "strategy": strategy_spec,
        "num_queries": evaluated,
        "metrics": metrics,
    }
    result_path.write_text(json.dumps(result_data, indent=2), encoding="utf-8")

    context.log.info(
        "Evaluated %s: %d queries, recall@5=%.3f, precision@5=%.3f, mrr=%.3f",
        partition_key,
        evaluated,
        metrics.get("recall@5", 0),
        metrics.get("precision@5", 0),
        metrics.get("mrr", 0),
    )

    return dg.MaterializeResult(
        metadata={
            "partition": dg.MetadataValue.text(partition_key),
            "num_queries": dg.MetadataValue.int(evaluated),
            **{k_: dg.MetadataValue.float(v) for k_, v in metrics.items()},
        }
    )


@dg.asset(
    group_name="evaluate",
    compute_kind="evaluation",
    deps=["eval_strategy_run"],
    description="Compare all evaluated strategy combos and produce a summary table",
)
def eval_comparison(context: AssetExecutionContext) -> dg.MaterializeResult:
    """Read all result JSONs and build a comparison markdown table."""
    if not EVAL_RESULTS_DIR.exists():
        context.log.warning("No eval results directory found")
        return dg.MaterializeResult(metadata={"status": dg.MetadataValue.text("no results found")})

    # Load all result JSONs (exclude comparison.md)
    result_files = sorted(EVAL_RESULTS_DIR.glob("*.json"))
    if not result_files:
        context.log.warning("No eval result JSON files found")
        return dg.MaterializeResult(
            metadata={"status": dg.MetadataValue.text("no result files found")}
        )

    all_results: list[dict] = []
    for path in result_files:
        all_results.append(json.loads(path.read_text(encoding="utf-8")))

    # Build comparison table
    metric_names = ["recall@5", "precision@5", "mrr", "avg_latency_ms"]
    md_lines = ["| Combo | Recall@5 | Precision@5 | MRR | Avg Latency (ms) |"]
    md_lines.append("| --- | --- | --- | --- | --- |")

    # Track best values for flagging
    best: dict[str, tuple[float, str]] = {}
    for r in all_results:
        r_metrics = r.get("metrics", {})
        for mn in metric_names:
            val = r_metrics.get(mn, 0.0)
            # For latency, lower is better; for others, higher is better
            if mn == "avg_latency_ms":
                if mn not in best or val < best[mn][0]:
                    best[mn] = (val, r["partition_key"])
            else:
                if mn not in best or val > best[mn][0]:
                    best[mn] = (val, r["partition_key"])

    for r in all_results:
        r_metrics = r.get("metrics", {})
        cells = []
        for mn in metric_names:
            val = r_metrics.get(mn, 0.0)
            fmt = f"{val:.1f}" if mn == "avg_latency_ms" else f"{val:.3f}"
            if best.get(mn, (None, None))[1] == r["partition_key"]:
                fmt = f"**{fmt}**"
            cells.append(fmt)
        md_lines.append(f"| {r['partition_key']} | {' | '.join(cells)} |")

    comparison_md = "\n".join(md_lines)

    # Write comparison file
    comparison_path = EVAL_RESULTS_DIR / "comparison.md"
    comparison_path.write_text(comparison_md, encoding="utf-8")

    context.log.info("Comparison table written to %s", comparison_path)
    return dg.MaterializeResult(
        metadata={
            "combos_compared": dg.MetadataValue.int(len(all_results)),
            "comparison": dg.MetadataValue.md(comparison_md),
        }
    )


def _unique_content_ids(results: list) -> list[str]:
    """Extract unique content_ids preserving first-occurrence order."""
    seen: set[str] = set()
    ids: list[str] = []
    for r in results:
        cid = r.content_id if hasattr(r, "content_id") else r.get("content_id", "")
        if cid and cid not in seen:
            seen.add(cid)
            ids.append(cid)
    return ids
