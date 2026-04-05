# Evaluation Harness

Snapshot-based retrieval evaluation for comparing RAG strategies.

## How It Works

1. `snapshot_eval` asset queries each RAG collection with the curated query set
2. For each query, retrieves top-5 results and compares against ground-truth `content_id`s
3. Computes metrics per collection, outputs a comparison markdown table in Dagster UI

## Metrics

| Metric | What it measures |
|---|---|
| **Recall@5** | Fraction of expected documents found in top-5 results |
| **Precision@5** | Fraction of top-5 results that are relevant |
| **MRR** | 1/rank of the first relevant result (higher = relevant result appears earlier) |
| **Avg Latency** | Mean query response time in milliseconds |

## Query Set (v2)

20 queries across 5 difficulty categories, curated against `raw_store_2026-04-05.db`.

### Categories

**Easy (4 queries)** — Near-verbatim title matches. Any embedding model should ace these. Serves as a sanity check that the retrieval pipeline is functional.

> Example: "data engineering trends 2026" → matches article titled "9 Data Engineering Trends in 2026..."

**Paraphrase (5 queries)** — Describes the problem or concept using different vocabulary than the article title. Tests whether the embedding model understands semantics beyond keyword matching.

> Example: "approaches to reduce repeated LLM API calls for similar user questions" → should find the Semantic Cache article, even though the query never mentions "cache"

**Buried Detail (4 queries)** — The answer is a specific fact or detail deep in the article body (a benchmark table, a statistic, a subsection topic). Not mentioned in the title or heading. Tests whether the embedding captures body content, especially relevant for models with short context windows (MiniLM's 256-token limit truncates most of each chunk).

> Example: "Using QLoRA with dual RTX 3090 GPUs for fine-tuning 8B parameter models" → detail buried in the training setup section of the knowledge graphs article

**Cross-Article (4 queries)** — The answer spans multiple articles that address the topic from different angles. A good retrieval system returns several relevant articles, not just the single closest title match.

> Example: "why organizations fail at becoming data-driven despite hiring analysts" → combines "We Hired 10 Data Scientists..." (satirical) with "Building a Data Team from 0 to 10" (practical)

**Negative (3 queries)** — The topic does not exist in the corpus. `expected_content_ids` is empty. A good retrieval system should return results with low confidence (high distance scores). Tests precision — a weak system will return false positives from keyword overlap.

> Example: "Apache Kafka consumer group lag monitoring and alerting" → Kafka is mentioned in passing in CDC/pipeline articles, but no article is actually about Kafka operations

### What Each Category Exposes

| Category | Better embeddings help? | Longer context helps? | Context enrichment helps? |
|---|---|---|---|
| Easy | No (already saturated) | No | No |
| Paraphrase | Yes (semantic understanding) | No | Partially |
| Buried detail | Partially | Yes (captures body text) | No |
| Cross-article | Yes | Partially | Yes (title disambiguation) |
| Negative | Yes (better similarity calibration) | No | Partially |

## Updating Queries

1. Run queries manually against the collection to inspect results
2. Record which `content_id`s are genuinely relevant
3. Add to `queries.py`
4. Bump `QUERY_SET_VERSION` when the set changes

Ground truth is at the **content_id level** (not chunk_id) so it survives re-chunking across strategies.

## Running

```bash
uv run poe eval
# or materialize snapshot_eval in Dagster UI
```

## Adding a New Collection to Evaluate

Edit `RAG_COLLECTIONS` in `assets.py`:

```python
RAG_COLLECTIONS = ["baseline", "nomic_embed"]
```
