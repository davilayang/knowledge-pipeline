# Dagster ops for retrieval quality evaluation.
# Uses op factory to generate one eval op per (collection, strategy) combo.

import json
import logging
import time
from datetime import UTC, datetime

import dagster as dg

from knowledge_pipeline.config import EVAL_RESULTS_DIR, LOCAL_RAW_STORE, SOURCE_RAW_STORE
from knowledge_pipeline.lib.eval import mrr, precision_at_k, recall_at_k
from knowledge_pipeline.lib.retrieval import build_strategy
from knowledge_pipeline.lib.store import count_contents
from knowledge_pipeline.lib.utils import get_embedding_model_for_collection, hash_file
from knowledge_pipeline.lib.vector_store import get_client, get_collection

from .queries import EVAL_QUERIES, QUERY_SET_VERSION
from .registry import EVAL_COMBOS, parse_combo

logger = logging.getLogger(__name__)

K = 5


# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------


@dg.op(
    description="Validate eval config: query set, dataset version, collections",
    out=dg.Out(dagster_type=dg.Nothing),
)
def eval_preflight_check(context: dg.OpExecutionContext) -> None:
    """Log eval configuration, dataset version, and verify collections."""
    # Query set
    context.log.info("Query set version: %s", QUERY_SET_VERSION)
    context.log.info("Queries: %d", len(EVAL_QUERIES))
    context.log.info("Date: %s", datetime.now(tz=UTC).isoformat())

    if not EVAL_QUERIES:
        raise dg.Failure("No eval queries defined — populate defs/evaluate/queries.py")

    # Dataset version
    if SOURCE_RAW_STORE.exists():
        corpus_hash = hash_file(SOURCE_RAW_STORE)
        context.log.info("Source dataset: %s", SOURCE_RAW_STORE.name)
        context.log.info("Corpus hash: %s", corpus_hash)
    else:
        context.log.warning("Source dataset not found: %s", SOURCE_RAW_STORE)

    if LOCAL_RAW_STORE.exists():
        local_hash = hash_file(LOCAL_RAW_STORE)
        row_count = count_contents(db_path=LOCAL_RAW_STORE)
        context.log.info("Local DB: %s (hash=%s, %d rows)", LOCAL_RAW_STORE, local_hash, row_count)
    else:
        context.log.warning("Local DB not found: %s", LOCAL_RAW_STORE)

    # Collections
    client = get_client()
    existing = {c.name for c in client.list_collections()}

    collection_names = {coll for coll, _ in (parse_combo(c) for c in EVAL_COMBOS)}
    for name in sorted(collection_names):
        if name in existing:
            em = get_embedding_model_for_collection(name)
            from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction

            ef = SentenceTransformerEmbeddingFunction(model_name=em)
            coll = get_collection(client=client, collection_name=name, embedding_function=ef)
            context.log.info("Collection '%s': %d chunks (model: %s)", name, coll.count(), em)
        else:
            context.log.warning("Collection '%s': NOT FOUND", name)

    missing = [n for n in collection_names if n not in existing]
    if len(missing) == len(collection_names):
        raise dg.Failure(f"No collections found: {sorted(missing)}")


# ---------------------------------------------------------------------------
# Per-(collection, strategy) eval op factory
# ---------------------------------------------------------------------------


def create_eval_op(collection_name: str, strategy_spec: str) -> dg.OpDefinition:
    """Factory: create an eval op for a single (collection, strategy) combo."""

    @dg.op(
        name=f"evaluate_{collection_name}__{strategy_spec}",
        description=(
            f"Run eval queries against '{collection_name}' "
            f"with '{strategy_spec}' retrieval strategy"
        ),
        ins={"ready": dg.In(dagster_type=dg.Nothing)},
    )
    def _eval(context: dg.OpExecutionContext) -> dict:
        client = get_client()
        existing = {c.name for c in client.list_collections()}

        if collection_name not in existing:
            context.log.warning("Collection '%s' not found, skipping", collection_name)
            return {
                "collection": collection_name,
                "strategy": strategy_spec,
                "status": "not_found",
                "metrics": {},
            }

        # Look up embedding model from strategies.yaml so queries use the correct embedder
        embedding_model = get_embedding_model_for_collection(collection_name)
        from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction

        ef = SentenceTransformerEmbeddingFunction(model_name=embedding_model)
        collection = get_collection(
            client=client, collection_name=collection_name, embedding_function=ef
        )
        if collection.count() == 0:
            context.log.warning("Collection '%s' is empty, skipping", collection_name)
            return {
                "collection": collection_name,
                "strategy": strategy_spec,
                "status": "empty",
                "metrics": {},
            }

        strategy = build_strategy(collection, strategy_spec)
        per_query: list[dict] = []

        for eq in EVAL_QUERIES:
            start = time.perf_counter()
            results = strategy.retrieve(eq.query, n_results=min(K, collection.count()))
            latency_ms = (time.perf_counter() - start) * 1000

            retrieved = _unique_content_ids(results)

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

        context.log.info(
            "Evaluated %d queries against '%s' with strategy '%s'",
            len(per_query),
            collection_name,
            strategy_spec,
        )
        return {
            "collection": collection_name,
            "strategy": strategy_spec,
            "status": "ok",
            "chunk_count": collection.count(),
            "metrics": per_query,
        }

    return _eval


# Generate one eval op per (collection, strategy) combo
eval_ops = [create_eval_op(coll, strat) for coll, strat in (parse_combo(c) for c in EVAL_COMBOS)]


# ---------------------------------------------------------------------------
# Aggregate + write report
# ---------------------------------------------------------------------------


@dg.op(description="Aggregate per-combo eval results into a comparison report")
def aggregate_results(context: dg.OpExecutionContext, eval_run_results: list[dict]) -> dict:
    """Compute overall and per-category metrics from raw query results."""
    report = {
        "query_set_version": QUERY_SET_VERSION,
        "timestamp": datetime.now(tz=UTC).isoformat(),
        "num_queries": len(EVAL_QUERIES),
        "combos": {},
    }

    for result in eval_run_results:
        coll_name = result["collection"]
        strat_name = result["strategy"]
        combo_key = f"{coll_name}__{strat_name}"

        if result["status"] != "ok":
            report["combos"][combo_key] = {
                "collection": coll_name,
                "strategy": strat_name,
                "status": result["status"],
            }
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

        report["combos"][combo_key] = {
            "collection": coll_name,
            "strategy": strat_name,
            "status": "ok",
            "chunk_count": result["chunk_count"],
            "overall": overall,
            "by_category": categories,
        }

    context.log.info("Aggregated results for %d combos", len(report["combos"]))
    return report


@dg.op(description="Write eval report to data/eval_results/ as JSON + markdown")
def write_report(context: dg.OpExecutionContext, report: dict) -> None:
    """Write JSON and markdown reports, log summary."""
    EVAL_RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(tz=UTC).strftime("%Y-%m-%dT%H-%M-%SZ")

    # JSON (machine-readable, for history/comparison)
    json_path = EVAL_RESULTS_DIR / f"eval_{timestamp}.json"
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    context.log.info("JSON report: %s", json_path)

    # Markdown (human-readable)
    md = _build_markdown(report)
    md_path = EVAL_RESULTS_DIR / f"eval_{timestamp}.md"
    md_path.write_text(md, encoding="utf-8")
    context.log.info("Markdown report: %s", md_path)

    context.log.info("\n%s", md)


def _build_markdown(report: dict) -> str:
    """Build a markdown summary from the report dict."""
    lines = [f"# Eval Report — {report['timestamp']}\n"]
    lines.append(f"Query set: {report['query_set_version']} ({report['num_queries']} queries)\n")

    # Overall comparison
    lines.append("## Overall\n")
    lines.append("| Collection | Strategy | Recall@5 | Precision@5 | MRR | Avg Latency |")
    lines.append("| --- | --- | --- | --- | --- | --- |")
    for combo_key, data in report["combos"].items():
        coll = data["collection"]
        strat = data["strategy"]
        if data.get("status") != "ok":
            lines.append(f"| {coll} | {strat} | — | — | — | {data['status']} |")
            continue
        o = data["overall"]
        lines.append(
            f"| {coll}"
            f" | {strat}"
            f" | {o['recall@5']:.3f}"
            f" | {o['precision@5']:.3f}"
            f" | {o['mrr']:.3f}"
            f" | {o['avg_latency_ms']:.0f}ms |"
        )

    # Per-category — collect all category names across all combos
    all_cats: list[str] = []
    for data in report["combos"].values():
        if data.get("status") == "ok":
            for cat in data["by_category"]:
                if cat not in all_cats:
                    all_cats.append(cat)

    for combo_key, data in report["combos"].items():
        if data.get("status") != "ok":
            continue
        coll = data["collection"]
        strat = data["strategy"]
        lines.append(f"\n## {coll} / {strat} — By Category\n")
        lines.append("| Category | Queries | Recall@5 | Precision@5 | MRR | Latency |")
        lines.append("| --- | --- | --- | --- | --- | --- |")
        cats = data["by_category"]
        for cat in all_cats:
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
    """Graph: preflight -> eval each (collection, strategy) combo -> aggregate -> write report."""
    ready = eval_preflight_check()
    results = [op(ready) for op in eval_ops]
    report = aggregate_results(eval_run_results=results)
    write_report(report)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _unique_content_ids(results: list) -> list[str]:
    """Extract unique content_ids from RetrievalResult objects.

    Preserves first-occurrence order.
    """
    seen: set[str] = set()
    ids: list[str] = []
    for r in results:
        cid = r.content_id
        if cid and cid not in seen:
            seen.add(cid)
            ids.append(cid)
    return ids
