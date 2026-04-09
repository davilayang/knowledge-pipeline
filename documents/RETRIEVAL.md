# Retrieval Strategies

How queries are matched against indexed newsletter chunks. Each strategy implements the same interface (`RetrievalStrategy` protocol) and returns ranked results with scores. All strategies live in `src/knowledge_pipeline/lib/retrieval/` and are registered in `registry.py`.

Configuration lives in `strategies.yaml` under `retrieval_strategies`.

---

## Cosine Similarity

**File:** `cosine.py` | **Spec:** `"cosine"` | **Latency:** ~130ms

The retrieval baseline. Embeds the query with the same model used at index time, then finds the nearest chunks by cosine distance in ChromaDB.

```
Query  →  embed(query)  →  ChromaDB top-K by distance  →  results
```

### Cosine Distance

Given query embedding **q** and document embedding **d**, cosine similarity is:

```
                        q . d
cos_sim(q, d)  =  ─────────────
                   ‖q‖ * ‖d‖
```

ChromaDB returns cosine **distance** (`1 - similarity`). We convert back:

```
score  =  1.0 - distance  =  cos_sim(q, d)
```

Score range is 0-1 (higher is better). A score of 1.0 means identical direction in embedding space.

**Strengths:** Fast, simple, no extra dependencies.
**Weaknesses:** Ranks by geometric proximity in embedding space only. Misses exact keyword matches if the embedding model doesn't capture them. Sensitive to embedding model quality and token limits (e.g. MiniLM truncates at 256 tokens).

---

## Cross-Encoder Reranking

**File:** `rerank.py` | **Spec:** `"rerank"` | **Latency:** ~700ms

Two-stage retrieval using a composition pattern. First retrieves a broad candidate set via an inner strategy (cosine), then re-scores every (query, document) pair with a cross-encoder.

```
Query  →  cosine top-20  →  cross-encoder rescores each pair  →  top-5
```

**Model:** `cross-encoder/ms-marco-MiniLM-L-6-v2` (22M params). Loaded lazily on first call.

**How cross-encoders differ from bi-encoders:**
- Bi-encoder (cosine): encodes query and document **separately**, compares vectors. Fast but no cross-attention.
- Cross-encoder (rerank): encodes query and document **together** with full attention. Sees token-level interactions ("reads" both at once). More accurate but O(n) per candidate.

**Score:** Raw cross-encoder logit (not bounded to 0-1; higher is better).

**Strengths:** Significantly improves ranking quality (MRR). The cross-encoder can understand nuanced query-document relationships that vector similarity misses.
**Weaknesses:** ~5x slower than cosine due to per-pair inference. Quality is bounded by the initial cosine candidate set.

---

## Hybrid (BM25 + Vector + RRF)

**File:** `hybrid.py` | **Spec:** `"hybrid"` | **Latency:** ~200ms

Runs two independent retrieval paths and merges results via Reciprocal Rank Fusion.

```
                ┌──  ChromaDB vector search  ──  ranked list A
Query  ──┤
                └──  BM25 sparse search      ──  ranked list B
                                                       │
                                    RRF merge  ←───────┘
                                        │
                                    top-K results
```

### BM25 (Best Matching 25)

A term-frequency scoring function (refined TF-IDF). For each query term:

```
score(q, d) = IDF(q) * (tf * (k1 + 1)) / (tf + k1 * (1 - b + b * |d| / avgdl))
```

- **IDF** — rare terms score higher ("chromadb" is more discriminating than "the")
- **TF saturation** — term frequency helps but with diminishing returns
- **Length normalization** — short matching docs aren't penalized vs. long ones

The BM25 index is built lazily on first `retrieve()` call from all documents in the ChromaDB collection (`collection.get()`). Tokenization is whitespace split + lowercasing.

### Reciprocal Rank Fusion (RRF)

Since BM25 scores and cosine distances are on different scales, RRF operates on **ranks** instead of raw scores:

```
RRF_score(doc) = sum over sources: 1 / (k + rank_in_source)
```

Where `k = 60` (constant from the original Cormack et al. paper). Documents appearing in **both** result sets get boosted:

| Scenario | Score |
|---|---|
| Ranked #1 in both sources | `1/61 + 1/61 = 0.0328` |
| Ranked #1 in one, #10 in other | `1/61 + 1/70 = 0.0307` |
| Ranked #1 in only one source | `1/61 = 0.0164` |

**Strengths:** Catches exact keyword matches that vector search misses. No extra model to load. Complementary signals — vector handles semantics, BM25 handles lexical precision.
**Weaknesses:** BM25 uses naive whitespace tokenization (no stemming or stopword removal). The BM25 index is rebuilt per strategy instance (not persisted).

---

## Strategy Composition

Strategies are composed via the registry in `registry.py`:

```python
_STRATEGY_BUILDERS = {
    "cosine": lambda col: CosineRetrieval(col),
    "rerank": lambda col: RerankRetrieval(CosineRetrieval(col)),
    "hybrid": lambda col: HybridRetrieval(col),
}
```

The `rerank` strategy wraps `cosine` — this composition pattern allows stacking. For example, a future `rerank+hybrid` could wrap hybrid with a cross-encoder pass.

---

## Evaluation

Each retrieval strategy is evaluated against every index strategy (chunking + embedding model) as defined by `eval_combos` in `strategies.yaml`. The format is `"collection__retrieval"` (double underscore).

Current combos:
```
baseline__cosine          baseline__rerank          baseline__hybrid
recursive_minilm__cosine  recursive_minilm__rerank  recursive_minilm__hybrid
bge__cosine               bge__rerank               bge__hybrid
```

### Metrics

| Metric | Formula | What it measures |
|---|---|---|
| **Recall@K** | `\|retrieved_K ∩ expected\| / \|expected\|` | Fraction of expected articles found in top-K results |
| **Precision@K** | `\|retrieved_K ∩ expected\| / K` | Fraction of top-K results that are relevant |
| **MRR** | `1 / rank_of_first_relevant` | Reciprocal rank of the first relevant result (rewards putting the right answer first) |
| **Latency** | wall-clock ms | Time per query in milliseconds |

Where `K = 5` in the current eval harness. MRR returns 0 if no relevant result is found.

Run `uv run poe eval` to evaluate all combos.

---

## Planned Strategies

| Strategy | Type | Key idea | Dependency |
|---|---|---|---|
| **HyDE** | Query-time | Generate hypothetical answer via LLM, embed that instead of the query | LLM API |
| **Fusion** | Query-time | Generate 3-5 query variants via LLM, retrieve for each, merge with RRF | LLM API |

Stubs exist in `hyde.py` and `fusion.py`.
