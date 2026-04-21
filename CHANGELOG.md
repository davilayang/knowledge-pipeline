# Changelog

All notable changes to this project will be documented in this file.

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)

## [Unreleased]

---

## [0.2.0] — 2026-04-20

### Added

- **LLM client** — `lib/llm.py` with LangChain wrapper (`generate`, `generate_structured`) for provider-agnostic LLM calls with Pydantic-validated structured output
- **Wiki config** — `wiki` section in `strategies.yaml` (synthesis model, page types, collection name, embedding model)
- **`langchain-openai`** dependency (replaces direct `openai` SDK)
- **CHANGELOG.md** — initial changelog covering project history

---

## [0.1.0] — 2026-04-20

Initial baseline. Dagster-based RAG strategy workbench with evaluation harness.

### Added

- **Index strategies** — four pluggable chunking + embedding combinations:
  - `idx_markdown_minilm` — markdown-aware chunking + MiniLM (baseline)
  - `idx_markdown_bge` — markdown chunking + BGE-small-en-v1.5
  - `idx_recursive_minilm` — recursive character splitting + MiniLM
  - `idx_semantic_minilm` — semantic chunking (embedding similarity splits) + MiniLM
- **Retrieval strategies** — four retrieval methods:
  - `cosine` — basic vector similarity
  - `rerank` — two-stage with cross-encoder reranking
  - `hybrid` — BM25 + vector + Reciprocal Rank Fusion
  - `rerank_hybrid` — hybrid candidates reranked by cross-encoder
- **Evaluation harness** — ops-based job comparing all (collection x retrieval) combos with recall@k, precision@k, MRR metrics across 40 curated queries
- **Chunking registry** — pluggable chunking strategies via `lib/chunking/registry.py`
- **Op factories** — `create_chunk_batch_op`, `create_embed_batch_op`, `create_index_op` for strategy-specific Dagster ops
- **Static dataset** — pinned `raw_store.db` snapshot for reproducible evaluation
- **Database backup job** — scheduled backup of SQLite and ChromaDB data
- **Docker deployment** — Dockerfiles and docker-compose with separate code location server
- **SSH tunnel task** — `uv run poe tunnel dagster` for remote UI access
- **Code locations** — split into `workbench/` (index + eval) and `pipelines/` (backup)
