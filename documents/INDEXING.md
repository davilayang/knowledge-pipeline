# Indexing Strategies

How newsletter content is chunked, embedded, and stored for retrieval. 

Each index strategy is a combination of a **chunking method** and an **embedding model**, 
producing a ChromaDB collection that retrieval strategies query against.

- Configuration lives in `strategies.yaml` under `index_strategies`. 
- Strategy code lives in `defs/workbench/idx_{chunking}_{embedding}/`.


## Pipeline

Every index strategy follows the same three-stage pipeline (defined in `defs/shared/op_factories.py`):

```
 ┌──────────┐     ┌──────────┐     ┌──────────┐     ┌──────────┐
 │Raw Store │ ──▶ │  Chunk   │ ──▶ │  Embed   │ ──▶ │  Index   │
 └──────────┘     └──────────┘     └──────────┘     └──────────┘
                    chunks/          embeddings/      ChromaDB
                    {strategy}/      {strategy}/      collection
```

---

## Chunking Methods

All chunking functions live in `lib/chunking/` and return `list[Chunk]` where `Chunk` has `text`, `heading`, and `index` fields.

### Markdown (header-aware)

Used by: `idx_markdown_minilm`, `idx_markdown_bge`

Splits on markdown heading boundaries (`#`, `##`, `###`) first, then recursively splits oversized sections by character boundaries. Preserves the nearest ancestor heading in each chunk's metadata.

**How it works:**
1. Split text at heading markers
2. Pack consecutive paragraphs under the same heading until the chunk size limit
3. If a section exceeds the limit, fall back to recursive character splitting within that section

**Strengths:** Respects document structure. Chunks tend to be topically coherent because they follow the author's own section boundaries.
**Weaknesses:** Assumes well-structured markdown. Poorly formatted articles produce uneven chunks.

### Recursive (character-based)

Used by: `idx_recursive_minilm`

Tries progressively smaller separators until chunks fit the size limit:

```
\n\n  →  \n  →  (space)  →  (empty string)
```

No awareness of markdown structure. A simpler, more general-purpose approach.

**Strengths:** Works on any text format. No structural assumptions.
**Weaknesses:** Can split mid-paragraph or mid-sentence at unfortunate boundaries. Loses heading context.

### Other Available Methods (not yet used in strategies)

| Method | Description |
|---|---|
| `markdown_syntax` | Pure `MarkdownTextSplitter` — respects code blocks and lists but no recursive fallback |
| `fixed` | Fixed-size character windows with overlap. Rigid boundaries, no semantic awareness |
| `token` | Splits by actual token count (via tiktoken). More precise than character-based |
| `sentence_transformer_token` | Uses the embedding model's own tokenizer for aligned splits |

### Parameters

All strategies currently use the same sizing:

| Parameter | Value | Notes |
|---|---|---|
| `chunk_size` | 800 tokens | Converted to ~3200 chars for character-based methods (4:1 ratio) |
| `chunk_overlap` | 100 tokens | Overlap between adjacent chunks to preserve context at boundaries |

**Character conversion** (for character-based splitters):

```
chunk_size_chars  =  chunk_size_tokens * CHARS_PER_TOKEN  =  800 * 4  =  3200
overlap_chars     =  chunk_overlap_tokens * CHARS_PER_TOKEN  =  100 * 4  =  400
```

**Effective coverage** — given `N` tokens of content, `chunk_size` of `S`, and `overlap` of `O`, the approximate number of chunks is:

```
n_chunks  ≈  ceil((N - O) / (S - O))  =  ceil((N - 100) / 700)
```

---

## Embedding Models

### all-MiniLM-L6-v2

Used by: `idx_markdown_minilm`, `idx_recursive_minilm`

| Property | Value |
|---|---|
| MTEB score | ~49 |
| Context window | 256 tokens |
| Dimensions | 384 |
| Size | 22M params |
| Provider | Hugging Face / ONNX Runtime (via ChromaDB default) |

The default ChromaDB embedding model. Fast and lightweight but its 256-token context means our 800-token chunks are **silently truncated** — only ~30% of each chunk is actually embedded. This is a known limitation that BGE addresses.

**Truncation ratio:** `min(context_window, chunk_tokens) / chunk_tokens = 256 / 800 ≈ 32%`

### BAAI/bge-small-en-v1.5

Used by: `idx_markdown_bge`

| Property | Value |
|---|---|
| MTEB score | ~62 |
| Context window | 512 tokens |
| Dimensions | 384 |
| Size | 33M params |
| Provider | Hugging Face (via SentenceTransformerEmbeddingFunction) |

A direct upgrade from MiniLM: +13 MTEB points and 2x context window. Still truncates 800-token chunks, but captures ~64% vs MiniLM's ~30%. Same embedding dimensions so retrieval logic is unchanged.

**Truncation ratio:** `min(context_window, chunk_tokens) / chunk_tokens = 512 / 800 = 64%`

---

## Current Strategies

### idx_markdown_minilm (Baseline)

```yaml
collection_name: baseline
chunking: markdown
embedding_model: all-MiniLM-L6-v2
```

The control experiment. Markdown-aware chunking with the default embedding model. All other strategies are compared against this.

**Assets:** `baseline_chunked` → `baseline_embedded` → `baseline_indexed`

### idx_recursive_minilm (Chunking Ablation)

```yaml
collection_name: recursive_minilm
chunking: recursive
embedding_model: all-MiniLM-L6-v2
```

Same embedding model as baseline, different chunking. Tests whether markdown-aware splitting actually matters vs. a simpler recursive approach. Isolates the effect of chunking method.

**Assets:** `recursive_minilm_chunked` → `recursive_minilm_embedded` → `recursive_minilm_indexed`

### idx_markdown_bge (Embedding Ablation)

```yaml
collection_name: bge
chunking: markdown
embedding_model: BAAI/bge-small-en-v1.5
```

Same chunking as baseline, upgraded embedding model. Tests whether a better model improves retrieval without changing the chunking. Isolates the effect of embedding quality.

**Assets:** `bge_chunked` → `bge_embedded` → `bge_indexed`

### Comparison

| | markdown_minilm | recursive_minilm | markdown_bge |
|---|---|---|---|
| **Role** | Control | Chunking ablation | Embedding ablation |
| **Chunking** | Markdown-aware | Recursive character | Markdown-aware |
| **Embedding** | MiniLM (MTEB 49) | MiniLM (MTEB 49) | BGE (MTEB 62) |
| **Context used** | ~30% of chunk | ~30% of chunk | ~64% of chunk |
| **Collection** | `baseline` | `recursive_minilm` | `bge` |

---

## Adding a New Index Strategy

1. Add an entry to `strategies.yaml` under `index_strategies` with `collection_name`, `chunking`, `chunk_size`, `chunk_overlap`, `embedding_model`
2. Create `defs/workbench/idx_{chunking}_{embedding}/` with `__init__.py` and `assets.py`
3. Call the op factories from `defs/shared/op_factories.py` with your strategy config
4. Register in `definitions.py`
5. Add eval combos to `strategies.yaml` (format: `"collection_name__retrieval_strategy"`)
6. Run: `uv run poe reset-indices && uv run poe index`

---

## Design Decisions

**Pre-computed embeddings.** Embeddings are stored as JSON files before being upserted to ChromaDB. This enables caching (re-index without re-embedding), inspection (debug individual chunks), and ablation (swap embedding model without re-chunking).

**Title/author prepending.** Every document string is prefixed with `"Title: {title} | Author: {author}\n"` before embedding. This helps the embedding model disambiguate chunks from different articles on similar topics at zero cost.

**Heading metadata.** The nearest markdown heading is preserved through the entire pipeline — from chunking through to search results. This gives retrieval consumers section-level context about where a chunk came from.

**Chunk ID uniqueness.** IDs follow the format `{content_id}::chunk{index}`, globally unique across all strategies and collections.
