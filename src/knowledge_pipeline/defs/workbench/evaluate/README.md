# Evaluation Harness

Op-based retrieval evaluation for comparing RAG strategies across (collection x retrieval) combos.

## How It Works

1. Op factory creates one eval op per `(collection, strategy)` combo from `EVAL_COMBOS`
2. Each op queries the collection using the specified `RetrievalStrategy`, retrieves top-5 results
3. Compares against ground-truth `content_id`s from the curated query set
4. Aggregates metrics across all combos and writes a markdown report to `data/eval_results/`

## Metrics

| Metric | What it measures |
|---|---|
| **Recall@5** | Fraction of expected documents found in top-5 results |
| **Precision@5** | Fraction of top-5 results that are relevant |
| **MRR** | 1/rank of the first relevant result (higher = relevant result appears earlier) |
| **Avg Latency** | Mean query response time in milliseconds |

## Query Set (v3)

36 queries across 11 categories, curated against `raw_store_2026-04-05.db`.

### Original Categories (v2)

**Easy (4 queries)** — Near-verbatim title matches. Sanity check that the pipeline works.

> Example: "data engineering trends 2026" → matches article titled "9 Data Engineering Trends in 2026..."

**Paraphrase (5 queries)** — Different vocabulary than the article title. Tests semantic understanding.

> Example: "approaches to reduce repeated LLM API calls for similar user questions" → Semantic Cache article

**Buried Detail (4 queries)** — Answer is a specific fact deep in the article body, not in title/heading.

> Example: "Using QLoRA with dual RTX 3090 GPUs for fine-tuning 8B parameter models"

**Cross-Article (4 queries)** — Answer spans multiple articles from different angles.

> Example: "why organizations fail at becoming data-driven despite hiring analysts"

**Negative (3 queries)** — Topic doesn't exist in corpus. Expected result: nothing relevant.

> Example: "Apache Kafka consumer group lag monitoring and alerting"

### New Categories (v3) — Strategy-Differentiating

These categories are designed so baseline cosine retrieval scores low (~0.2-0.5 Recall@5), giving headroom to measure improvement from specific advanced strategies.

**Lexical Gap (4 queries)** — Casual/symptom language with zero vocabulary overlap to solution articles. Tests the query-document semantic gap that HyDE, Multi-query/Fusion, and Step-back prompting are designed to bridge.

> Example: "my AI keeps making stuff up and I don't know how to stop it" → hallucination/CRAG articles

**Scattered Evidence (3 queries)** — Answer requires 4+ articles. One prolific article can dominate all K=5 slots via many high-scoring chunks, crowding out others. Tests Query decomposition, MMR diversity reranking, and Deduplication.

> Example: "comprehensive overview of all the ways to improve a RAG pipeline end-to-end" → expects 5 RAG articles

**Conversational (3 queries)** — Natural user questions with implicit information need, no technical vocabulary. Tests Multi-query/Fusion, HyDE, and Adaptive RAG.

> Example: "we built a chatbot but the answers are mediocre, where should we look first"

**Exact Term (4 queries)** — Rare technical terms, model names, or acronyms that MiniLM embeds poorly. Tests Hybrid BM25+vector (exact token matching) and Cross-encoder reranking.

> Example: "Liquid LFM 2.5 1.2B parameter model benchmarks"

**Dense Haystack (3 queries)** — Specific fact (statistic, number) diluted inside an 800-token chunk whose dominant content is broader. Tests Proposition-based indexing and Parent-child chunking.

> Example: "what accuracy did naive RAG achieve before applying advanced strategies" → "60%" buried in 11 strategies article

**Negation (3 queries)** — Explicit negative constraint ("WITHOUT", "NOT") that bi-encoders cannot encode. Tests Hybrid BM25 (boolean exclusion) and Cross-encoder reranking (reading comprehension).

> Example: "RAG techniques that improve retrieval quality WITHOUT any additional LLM calls"

### Strategy-to-Category Map

Each category is the "home turf" where a specific strategy should show its biggest improvement:

| Category | Primary strategies that help |
|---|---|
| lexical_gap | HyDE, Multi-query/Fusion, Step-back |
| scattered_evidence | Query decomposition, MMR, Deduplication |
| conversational | Multi-query/Fusion, HyDE |
| exact_term | Hybrid BM25+vector |
| dense_haystack | Proposition indexing, Parent-child chunking |
| negation | Hybrid BM25, Cross-encoder reranking |

## Updating Queries

1. Run queries manually against the collection to inspect results
2. Record which `content_id`s are genuinely relevant
3. Add to `queries.py`
4. Bump `QUERY_SET_VERSION` when the set changes

Ground truth is at the **content_id level** (not chunk_id) so it survives re-chunking across strategies.

## Running

```bash
uv run poe eval
```

## Adding a New Strategy to Evaluate

1. Implement `RetrievalStrategy` in `lib/retrieval/{name}.py`
2. Register in `lib/retrieval/registry.py` (`_STRATEGY_BUILDERS` dict)
3. Add combo to `EVAL_COMBOS` in `registry.py` (format: `"collection__strategy"`)
4. Run eval: `uv run poe eval`
