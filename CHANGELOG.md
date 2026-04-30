# Changelog

All notable changes to this project will be documented in this file.

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)

## [Unreleased]

### Added

- **uv workspace scaffolding** (Phase A, PR 1) — five empty package skeletons under `packages/{domains,workflows,retrievers,evals,orchestrators}/`, each with its own `pyproject.toml`. Cross-package import discipline encoded in deps:
  - `domains` — pure data layer (pydantic, psycopg, pyyaml, python-frontmatter); no LLM/ML/Dagster deps
  - `workflows` — depends on domains + retrievers; adds langgraph, langgraph-checkpoint-postgres, langchain-openai, langchain-anthropic, langfuse
  - `retrievers` — depends on domains; adds chromadb, sentence-transformers, rank_bm25
  - `evals` — depends on domains + workflows + retrievers; adds ragas
  - `orchestrators` — depends on all four others; the only package allowed to depend on Dagster
- **Workspace root** — `[tool.uv.workspace]` + `[tool.uv.sources]` declaring the 5 members; new poe tasks for `wiki-ingest`, `wiki-lint`, `research`, `wiki-eval`, `rag-eval`, `reset-wiki`, `reset-checkpoints`, `reset-everything`
- **Postgres `knowledge_pipeline` database** — idempotent `docker/postgres/init/01-create-knowledge-db.sh` mounted into the existing postgres service via `/docker-entrypoint-initdb.d`; reuses the Dagster Postgres instance rather than running a second one
- **`packages/domains/src/domains/wiki/schema/wiki.sql`** — Postgres schema for wiki state: `wiki.processed` (PK `item_id, source_type`), `wiki.pages` (PK `entity_id`, jsonb columns for `related`/`sources`/`source_types`), `wiki.aliases` (UNIQUE `alias`, indexed by `entity_id`)

### Notes

- Existing `src/knowledge_pipeline/` layout untouched; all 90 tests continue to pass. Code moves into the new packages happen in PR 2.
- `uv sync --all-packages` (not bare `uv sync`) is required to install the new workspace members in editable mode until PR 2.

---

## [0.3.0] — 2026-04-28

### Added

- **Wiki synthesis pipeline** — LLM-powered knowledge distillation that reads raw articles and produces wiki pages
  - Entity extraction (gpt-4.1-nano) identifies concepts, tools, and trends
  - Page synthesis (gpt-4.1-mini) creates or updates wiki pages per entity
  - Asset-based Dagster architecture (`wiki_synthesized`, `wiki_pending`, `wiki_index_updated`)
- **`lib/wiki/`** — core library with no Dagster dependencies:
  - `types.py` — Pydantic models (WikiPage, ExtractedEntity, ExtractionResult)
  - `io.py` — markdown + YAML frontmatter read/write with atomic writes
  - `aliases.py` — entity alias resolution with fuzzy matching (difflib, 0.85 threshold)
  - `state.py` — SQLite state tracking (WAL mode, transactional updates)
  - `sources.py` — source adapters (RawStoreSource, LocalFileSource)
  - `ingest.py` — orchestration: extract → synthesize → write → update state
  - `prompts.py` — LLM system/user prompts
- **Robustness** — atomic file writes (`os.replace`), transactional state DB, LLM output validation, staged alias persistence

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
