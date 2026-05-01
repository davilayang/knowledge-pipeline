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

### Changed

- **Workspace package pip-names prefixed with `knowledge-`** — `domains` → `knowledge-domains`, `workflows` → `knowledge-workflows`, `retrievers` → `knowledge-retrievers`, `evals` → `knowledge-evals`, `orchestrators` → `knowledge-orchestrators`. Matches the `newsletter-assistant` workspace pattern: prefix lives only in `pyproject.toml` `dependencies` and `[tool.uv.sources]` keys; import paths stay plain (`from domains import ...`). Pre-empts cross-project name collisions if any other knowledge-* package gets co-installed (e.g. when `newsletter-assistant` consumes `knowledge-workflows`).

- **Code migrated into the 5 packages** (Phase A, PR 2) — the old `src/knowledge_pipeline/` tree has been split across the new packages and deleted. New layout:
  - `domains/`: `store.py`, `wiki/{types,io,aliases,sources}.py`, `wiki/schema/wiki.sql`
  - `retrievers/`: `chunking/`, `postprocess/`, `retrieval/` (cosine, hybrid, rerank, fusion, registry — HyDE moved to workflows), `vector_store/chroma.py`
  - `workflows/`: `llm.py`, `wiki/{prompts,state,ingest}.py`, `agents/nodes/query_rewrite.py` (was `lib/retrieval/hyde.py`)
  - `evals/`: `rag.py` (was `lib/eval.py`)
  - `orchestrators/`: `definitions.py`, `config.py`, `strategies.{py,yaml}` (the `.py` was `lib/utils.py`), and the entire `defs/` tree (`shared/`, `workbench/`, `pipelines/`)
- **Boundary fixes** — `domains.store` and `retrievers.vector_store.chroma` no longer import `orchestrators.config`. Path arguments (`db_path`, `chroma_path`) are now passed in by the caller. `db_path` is keyword-only across all `domains.store` functions for clarity. `HyDE` retrieval has an LLM dep so it lives in `workflows.agents.nodes.query_rewrite`, not in `retrievers`.

### Changed

- **Workspace package pip-names prefixed with `knowledge-`** — `domains` → `knowledge-domains`, `workflows` → `knowledge-workflows`, `retrievers` → `knowledge-retrievers`, `evals` → `knowledge-evals`, `orchestrators` → `knowledge-orchestrators`. Matches the `newsletter-assistant` workspace pattern: prefix lives only in `pyproject.toml` `dependencies` and `[tool.uv.sources]` keys; import paths stay plain (`from domains import ...`). Pre-empts cross-project name collisions if any other knowledge-* package gets co-installed (e.g. when `newsletter-assistant` consumes `knowledge-workflows`).
- **Root project shape** — `pyproject.toml` is now a virtual project: `[project].dependencies = [knowledge-domains, knowledge-workflows, knowledge-retrievers, knowledge-evals, knowledge-orchestrators]`, no `[build-system]`, no `[tool.hatch.*]`. Bare `uv sync` (without `--all-packages`) installs the full workspace because all 5 members are listed as deps explicitly.
- **Dagster module** — `[tool.dagster].module_name` is now `orchestrators.definitions` (single merged Definitions module, replacing the two-code-location split). `poe dev/index/backup/eval` poe tasks updated. `configs/workspace.yaml` location_name and `docker/code/Dockerfile` `-m` flag also point at `orchestrators.definitions`.

### Removed

- **`src/knowledge_pipeline/`** — the entire old source tree, replaced by `packages/{domains,workflows,retrievers,evals,orchestrators}/`. All tests pass against the new layout.

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
