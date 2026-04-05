# Snapshot-based evaluation: run curated queries against all RAG collections
# and compute retrieval metrics for comparison.

import logging
import time

import dagster as dg
from dagster import AssetExecutionContext

from knowledge_pipeline.lib.eval import mrr, precision_at_k, recall_at_k
from knowledge_pipeline.lib.vector_store import get_client, get_collection

from .queries import EVAL_QUERIES

logger = logging.getLogger(__name__)

# Collections to evaluate — add new ones as strategies are created.
RAG_COLLECTIONS = ["baseline"]


@dg.asset(
    group_name="evaluate",
    compute_kind="evaluation",
    description="Run curated queries against all RAG collections and compare retrieval metrics",
)
def snapshot_eval(context: AssetExecutionContext) -> dg.MaterializeResult:
    """Evaluate each RAG collection on the curated query set."""
    if not EVAL_QUERIES:
        context.log.warning("No eval queries defined — populate defs/evaluate/queries.py")
        return dg.MaterializeResult(
            metadata={"status": dg.MetadataValue.text("no queries defined")}
        )

    k = 5
    client = get_client()

    # Discover which collections actually exist
    existing = {c.name for c in client.list_collections()}
    collections_to_eval = [name for name in RAG_COLLECTIONS if name in existing]

    if not collections_to_eval:
        context.log.warning("No RAG collections found to evaluate")
        return dg.MaterializeResult(
            metadata={"status": dg.MetadataValue.text("no collections found")}
        )

    # Per-strategy aggregated metrics
    results: dict[str, dict[str, float]] = {}

    for coll_name in collections_to_eval:
        collection = get_collection(client=client, collection_name=coll_name)
        if collection.count() == 0:
            context.log.info("Skipping empty collection: %s", coll_name)
            continue

        total_recall = 0.0
        total_precision = 0.0
        total_mrr = 0.0
        total_latency = 0.0
        evaluated = 0

        for eq in EVAL_QUERIES:
            start = time.perf_counter()
            query_results = collection.query(
                query_texts=[eq.query],
                n_results=min(k, collection.count()),
                include=["metadatas"],
            )
            latency_ms = (time.perf_counter() - start) * 1000

            # Extract content_ids from retrieved chunks
            metas = query_results["metadatas"][0] if query_results["metadatas"] else []
            retrieved_content_ids = _unique_content_ids(metas)

            total_recall += recall_at_k(retrieved_content_ids, eq.expected_content_ids, k)
            total_precision += precision_at_k(retrieved_content_ids, eq.expected_content_ids, k)
            total_mrr += mrr(retrieved_content_ids, eq.expected_content_ids)
            total_latency += latency_ms
            evaluated += 1

        if evaluated > 0:
            results[coll_name] = {
                "recall@5": total_recall / evaluated,
                "precision@5": total_precision / evaluated,
                "mrr": total_mrr / evaluated,
                "avg_latency_ms": total_latency / evaluated,
            }

    # Build comparison table
    md_lines = ["| Collection | Recall@5 | Precision@5 | MRR | Avg Latency (ms) |"]
    md_lines.append("| --- | --- | --- | --- | --- |")
    for coll_name, metrics in results.items():
        md_lines.append(
            f"| {coll_name}"
            f" | {metrics['recall@5']:.3f}"
            f" | {metrics['precision@5']:.3f}"
            f" | {metrics['mrr']:.3f}"
            f" | {metrics['avg_latency_ms']:.1f} |"
        )

    context.log.info("Evaluation complete for %d collections", len(results))
    return dg.MaterializeResult(
        metadata={
            "collections_evaluated": dg.MetadataValue.int(len(results)),
            "num_queries": dg.MetadataValue.int(len(EVAL_QUERIES)),
            "comparison": dg.MetadataValue.md("\n".join(md_lines)),
        }
    )


def _unique_content_ids(metadatas: list[dict]) -> list[str]:
    """Extract unique content_ids preserving first-occurrence order."""
    seen: set[str] = set()
    ids: list[str] = []
    for m in metadatas:
        cid = m.get("content_id", "")
        if cid and cid not in seen:
            seen.add(cid)
            ids.append(cid)
    return ids
