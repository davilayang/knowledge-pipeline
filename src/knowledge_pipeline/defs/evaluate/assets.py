# Snapshot-based evaluation: run curated queries against all RAG collections
# and compute retrieval metrics for comparison.

import logging
import time
from collections import defaultdict

import dagster as dg
from dagster import AssetExecutionContext

from knowledge_pipeline.lib.eval import mrr, precision_at_k, recall_at_k
from knowledge_pipeline.lib.vector_store import get_client, get_collection

from .queries import EVAL_QUERIES, QUERY_SET_VERSION

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

    # Per-strategy overall and per-category metrics
    overall: dict[str, dict[str, float]] = {}
    by_category: dict[str, dict[str, dict[str, float]]] = {}

    for coll_name in collections_to_eval:
        collection = get_collection(client=client, collection_name=coll_name)
        if collection.count() == 0:
            context.log.info("Skipping empty collection: %s", coll_name)
            continue

        totals = defaultdict(lambda: {"r": 0.0, "p": 0.0, "m": 0.0, "lat": 0.0, "n": 0})
        all_r, all_p, all_m, all_lat, all_n = 0.0, 0.0, 0.0, 0.0, 0

        for eq in EVAL_QUERIES:
            start = time.perf_counter()
            query_results = collection.query(
                query_texts=[eq.query],
                n_results=min(k, collection.count()),
                include=["metadatas"],
            )
            latency_ms = (time.perf_counter() - start) * 1000

            metas = query_results["metadatas"][0] if query_results["metadatas"] else []
            retrieved_content_ids = _unique_content_ids(metas)

            r = recall_at_k(retrieved_content_ids, eq.expected_content_ids, k)
            p = precision_at_k(retrieved_content_ids, eq.expected_content_ids, k)
            m = mrr(retrieved_content_ids, eq.expected_content_ids)

            cat = totals[eq.category]
            cat["r"] += r
            cat["p"] += p
            cat["m"] += m
            cat["lat"] += latency_ms
            cat["n"] += 1

            all_r += r
            all_p += p
            all_m += m
            all_lat += latency_ms
            all_n += 1

        if all_n > 0:
            overall[coll_name] = {
                "recall@5": all_r / all_n,
                "precision@5": all_p / all_n,
                "mrr": all_m / all_n,
                "avg_latency_ms": all_lat / all_n,
            }
            by_category[coll_name] = {
                cat: {
                    "recall@5": v["r"] / v["n"],
                    "precision@5": v["p"] / v["n"],
                    "mrr": v["m"] / v["n"],
                    "avg_latency_ms": v["lat"] / v["n"],
                    "num_queries": v["n"],
                }
                for cat, v in totals.items()
            }

    # Build overall comparison table
    md = ["## Overall\n"]
    md.append("| Collection | Recall@5 | Precision@5 | MRR | Avg Latency |")
    md.append("| --- | --- | --- | --- | --- |")
    for coll_name, m in overall.items():
        md.append(
            f"| {coll_name}"
            f" | {m['recall@5']:.3f}"
            f" | {m['precision@5']:.3f}"
            f" | {m['mrr']:.3f}"
            f" | {m['avg_latency_ms']:.0f}ms |"
        )

    # Build per-category breakdown for each collection
    cat_order = ["easy", "paraphrase", "buried", "cross", "negative"]
    for coll_name, cats in by_category.items():
        md.append(f"\n## {coll_name} — By Category\n")
        md.append("| Category | Queries | Recall@5 | Precision@5 | MRR | Avg Latency |")
        md.append("| --- | --- | --- | --- | --- | --- |")
        for cat in cat_order:
            if cat in cats:
                c = cats[cat]
                md.append(
                    f"| {cat}"
                    f" | {c['num_queries']}"
                    f" | {c['recall@5']:.3f}"
                    f" | {c['precision@5']:.3f}"
                    f" | {c['mrr']:.3f}"
                    f" | {c['avg_latency_ms']:.0f}ms |"
                )

    context.log.info("Evaluation complete for %d collections", len(overall))
    return dg.MaterializeResult(
        metadata={
            "query_set_version": dg.MetadataValue.text(QUERY_SET_VERSION),
            "collections_evaluated": dg.MetadataValue.int(len(overall)),
            "num_queries": dg.MetadataValue.int(len(EVAL_QUERIES)),
            "comparison": dg.MetadataValue.md("\n".join(md)),
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
