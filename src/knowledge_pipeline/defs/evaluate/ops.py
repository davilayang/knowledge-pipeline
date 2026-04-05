# Dagster ops for retrieval quality evaluation.
# Uses op factory to generate one eval op per RAG collection.

import json
import logging
import time
from datetime import UTC, datetime

import dagster as dg

from knowledge_pipeline.config import EVAL_RESULTS_DIR
from knowledge_pipeline.lib.eval import mrr, precision_at_k, recall_at_k
from knowledge_pipeline.lib.vector_store import get_client, get_collection

from .queries import EVAL_QUERIES, QUERY_SET_VERSION

logger = logging.getLogger(__name__)

K = 5

# Collections to evaluate — add new ones as strategies are created.
RAG_COLLECTIONS = ["baseline"]


# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------


@dg.op(
    description="Validate eval config: query set version, collection availability",
    out=dg.Out(dagster_type=dg.Nothing),
)
def preflight_check(context: dg.OpExecutionContext) -> None:
    """Log eval configuration and verify collections exist."""
    client = get_client()
    existing = {c.name for c in client.list_collections()}

    context.log.info("Query set version: %s", QUERY_SET_VERSION)
    context.log.info("Queries: %d", len(EVAL_QUERIES))
    context.log.info("Date: %s", datetime.now(tz=UTC).isoformat())

    for name in RAG_COLLECTIONS:
        if name in existing:
            coll = get_collection(client=client, collection_name=name)
            context.log.info("Collection '%s': %d chunks", name, coll.count())
        else:
            context.log.warning("Collection '%s': NOT FOUND", name)

    if not EVAL_QUERIES:
        raise dg.Failure("No eval queries defined — populate defs/evaluate/queries.py")

    missing = [n for n in RAG_COLLECTIONS if n not in existing]
    if len(missing) == len(RAG_COLLECTIONS):
        raise dg.Failure(f"No collections found: {missing}")


# ---------------------------------------------------------------------------
# Per-collection eval op factory
# ---------------------------------------------------------------------------


def create_eval_op(collection_name: str) -> dg.OpDefinition:
    """Factory: create an eval op for a single RAG collection."""

    @dg.op(
        name=f"eval_{collection_name}",
        description=f"Run eval queries against '{collection_name}' collection",
        ins={"ready": dg.In(dagster_type=dg.Nothing)},
    )
    def _eval(context: dg.OpExecutionContext) -> dict:
        client = get_client()
        existing = {c.name for c in client.list_collections()}

        if collection_name not in existing:
            context.log.warning("Collection '%s' not found, skipping", collection_name)
            return {"collection": collection_name, "status": "not_found", "metrics": {}}

        collection = get_collection(client=client, collection_name=collection_name)
        if collection.count() == 0:
            context.log.warning("Collection '%s' is empty, skipping", collection_name)
            return {"collection": collection_name, "status": "empty", "metrics": {}}

        per_query: list[dict] = []

        for eq in EVAL_QUERIES:
            start = time.perf_counter()
            results = collection.query(
                query_texts=[eq.query],
                n_results=min(K, collection.count()),
                include=["metadatas"],
            )
            latency_ms = (time.perf_counter() - start) * 1000

            metas = results["metadatas"][0] if results["metadatas"] else []
            retrieved = _unique_content_ids(metas)

            r = recall_at_k(retrieved, eq.expected_content_ids, K)
            p = precision_at_k(retrieved, eq.expected_content_ids, K)
            m = mrr(retrieved, eq.expected_content_ids)

            per_query.append(
                {
                    "query": eq.query,
                    "category": eq.category,
                    "recall": r,
                    "precision": p,
                    "mrr": m,
                    "latency_ms": latency_ms,
                    "retrieved_ids": retrieved,
                    "expected_ids": eq.expected_content_ids,
                }
            )

        context.log.info("Evaluated %d queries against '%s'", len(per_query), collection_name)
        return {
            "collection": collection_name,
            "status": "ok",
            "chunk_count": collection.count(),
            "metrics": per_query,
        }

    return _eval


# Generate one eval op per collection
eval_ops = [create_eval_op(name) for name in RAG_COLLECTIONS]


# ---------------------------------------------------------------------------
# Aggregate + write report
# ---------------------------------------------------------------------------


@dg.op(description="Aggregate per-collection eval results into a comparison report")
def aggregate_results(context: dg.OpExecutionContext, eval_results: list[dict]) -> dict:
    """Compute overall and per-category metrics from raw query results."""
    report = {
        "query_set_version": QUERY_SET_VERSION,
        "timestamp": datetime.now(tz=UTC).isoformat(),
        "num_queries": len(EVAL_QUERIES),
        "collections": {},
    }

    for result in eval_results:
        coll_name = result["collection"]
        if result["status"] != "ok":
            report["collections"][coll_name] = {"status": result["status"]}
            continue

        queries = result["metrics"]
        by_cat: dict[str, dict] = {}

        for q in queries:
            cat = q["category"]
            if cat not in by_cat:
                by_cat[cat] = {"r": 0, "p": 0, "m": 0, "lat": 0, "n": 0}
            by_cat[cat]["r"] += q["recall"]
            by_cat[cat]["p"] += q["precision"]
            by_cat[cat]["m"] += q["mrr"]
            by_cat[cat]["lat"] += q["latency_ms"]
            by_cat[cat]["n"] += 1

        n = len(queries)
        overall = {
            "recall@5": sum(q["recall"] for q in queries) / n,
            "precision@5": sum(q["precision"] for q in queries) / n,
            "mrr": sum(q["mrr"] for q in queries) / n,
            "avg_latency_ms": sum(q["latency_ms"] for q in queries) / n,
        }
        categories = {
            cat: {
                "num_queries": v["n"],
                "recall@5": v["r"] / v["n"],
                "precision@5": v["p"] / v["n"],
                "mrr": v["m"] / v["n"],
                "avg_latency_ms": v["lat"] / v["n"],
            }
            for cat, v in by_cat.items()
        }

        report["collections"][coll_name] = {
            "status": "ok",
            "chunk_count": result["chunk_count"],
            "overall": overall,
            "by_category": categories,
        }

    context.log.info("Aggregated results for %d collections", len(report["collections"]))
    return report


@dg.op(description="Write eval report to data/eval_results/ as JSON and log markdown summary")
def write_report(context: dg.OpExecutionContext, report: dict) -> None:
    """Write JSON report and log a markdown summary."""
    EVAL_RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(tz=UTC).strftime("%Y-%m-%dT%H-%M-%SZ")
    path = EVAL_RESULTS_DIR / f"eval_{timestamp}.json"
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    context.log.info("Report written to %s", path)

    # Log markdown summary
    md = _build_markdown(report)
    context.log.info("\n%s", md)


def _build_markdown(report: dict) -> str:
    """Build a markdown summary from the report dict."""
    lines = [f"# Eval Report — {report['timestamp']}\n"]
    lines.append(f"Query set: {report['query_set_version']} ({report['num_queries']} queries)\n")

    # Overall comparison
    lines.append("## Overall\n")
    lines.append("| Collection | Recall@5 | Precision@5 | MRR | Avg Latency |")
    lines.append("| --- | --- | --- | --- | --- |")
    for name, data in report["collections"].items():
        if data.get("status") != "ok":
            lines.append(f"| {name} | — | — | — | {data['status']} |")
            continue
        o = data["overall"]
        lines.append(
            f"| {name}"
            f" | {o['recall@5']:.3f}"
            f" | {o['precision@5']:.3f}"
            f" | {o['mrr']:.3f}"
            f" | {o['avg_latency_ms']:.0f}ms |"
        )

    # Per-category
    cat_order = ["easy", "paraphrase", "buried", "cross", "negative"]
    for name, data in report["collections"].items():
        if data.get("status") != "ok":
            continue
        lines.append(f"\n## {name} — By Category\n")
        lines.append("| Category | Queries | Recall@5 | Precision@5 | MRR | Latency |")
        lines.append("| --- | --- | --- | --- | --- | --- |")
        cats = data["by_category"]
        for cat in cat_order:
            if cat in cats:
                c = cats[cat]
                lines.append(
                    f"| {cat}"
                    f" | {c['num_queries']}"
                    f" | {c['recall@5']:.3f}"
                    f" | {c['precision@5']:.3f}"
                    f" | {c['mrr']:.3f}"
                    f" | {c['avg_latency_ms']:.0f}ms |"
                )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Graph
# ---------------------------------------------------------------------------


@dg.graph()
def eval_graph():
    """Graph: preflight → eval each collection → aggregate → write report."""
    ready = preflight_check()
    results = [op(ready) for op in eval_ops]
    report = aggregate_results(eval_results=results)
    write_report(report)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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
