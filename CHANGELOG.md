# Changelog

All notable changes to this project will be documented in this file.

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)

## [Unreleased]

---

## [0.6.0] — 2026-05-06

Revamp DAG "backup_databases" to "backup_readings". The tasks are configured following best practices from "dagster-open-plaform" project. DAG will backup sqlite databases in local and on cloud at Google Drive.


## [0.5.0] — 2026-05-06

Phase B — LangGraph wiki synthesis migration. The wiki synthesis pipeline is now a checkpointed LangGraph workflow with Send-API per-entity fan-out, transactional Postgres commits, and dynamic-partitioned Dagster materialization. Manual real-data validation deferred to a follow-up PR.

- **`workflows/shared/`** — new home for cross-workflow plumbing. `checkpointer.get_checkpointer(db_url=None)` is a context manager that yields a `langgraph.checkpoint.postgres.PostgresSaver` bound to a fresh psycopg connection; falls back to `DATABASE_URL` env var; calls `setup()` on entry. `observability.get_langfuse_callback()` returns a process-cached `langfuse.langchain.CallbackHandler` when `LANGFUSE_PUBLIC_KEY` is set, otherwise `None` (no warning).
- **`workflows/llm.py`** — both `generate` and `generate_structured` now pass `config={"callbacks": [...]}` to LangChain when the Langfuse callback is configured. No-op when env unset; existing behavior unchanged.
- **`domains/wiki/state.py`** — Postgres helpers backing the new workflow's terminal commit. Pure functions taking a `psycopg.Connection`; callers manage transactions. `insert_processed`, `get_processed_ids`, `get_failed`, `upsert_page`, `get_page`, `get_all_pages`, `insert_aliases_idempotent` (uses `ON CONFLICT (alias) DO NOTHING` for concurrent-partition safety), `snapshot_aliases` (reads aliases into the existing in-memory `AliasStore`).
- **`pytest-postgresql>=6.0,<8.0`** added to root dev deps. `tests/conftest.py` exposes a `wiki_pg` fixture that yields a fresh psycopg connection to a temp Postgres with `wiki.sql` loaded — used by `tests/wiki/test_state_pg.py` (11 new tests covering all helpers plus PK/upsert/concurrency edges).
- **`workflows/wiki_synthesis/`** — the new LangGraph workflow that replaces `workflows/wiki/ingest.py` (the legacy folder is now removed; see "PR 3 — rewire and cleanup" below). Files:
  - `graph.py` — parent `StateGraph` (one document per invocation). `extract_entities` → conditional fan-out via `langgraph.types.Send` → per-entity sub-graph → `commit`. The `WikiSynthesisState` TypedDict declares an `Annotated[list[dict], operator.add]` reducer on `entity_results` so concurrent sub-graphs concatenate their results into the parent state without collision.
  - `entity_graph.py` — per-entity sub-graph with one node (`process_entity`) and a restricted `EntityWorkflowOutput` schema so only `entity_results` flows back to the parent (avoids the `InvalidUpdateError` that LangGraph 1.x raises when multiple Sends try to write the parent's input keys).
  - `nodes.py` — `extract_entities` snapshots aliases from Postgres, calls the extraction LLM, stages new aliases for commit. `commit` opens one Postgres transaction and writes `wiki.pages` rows for each successful entity, `wiki.aliases` for staged tuples (`ON CONFLICT DO NOTHING`), and the single `wiki.processed` row — atomic per the plan's "same transaction" rule. Status mapping covers ok / error / skipped / partial-success.
  - `parsing.py` — pure helpers (`parse_llm_page_output`, `check_h2_preservation`, `slug_from_id`) lifted out of legacy `ingest.py` so the new workflow doesn't depend on the soon-to-be-deleted module.
- **`workflows/wiki_synthesis/` invocation pattern** — caller compiles the graph with a checkpointer from `workflows.shared.checkpointer.get_checkpointer()` and invokes with `thread_id=f"wiki_synthesis__{item_id}"`. Replay after crash resumes from per-entity sub-graph checkpoints; only failed entities re-run the synthesis LLM call.
- **`wiki.processed.status` CHECK constraint** added to `wiki.sql` — values must be `'ok'`, `'error'`, or `'skipped'`. Prevents silent drift if a future caller writes a different string.
- **Extraction failures now write a status='error' processed row** (new behavior — legacy raised and left no DB footprint, causing infinite Dagster retries on a permanent extraction failure).
- **Parity tests** ported from `tests/wiki/test_ingest.py` to `tests/wiki_synthesis/{test_parsing,test_stage_aliases,test_graph}.py`. 16 new tests; LLM calls mocked at import locations, Postgres assertions run against the real `wiki_pg` fixture so the transactional commit path is genuinely exercised.
- **`workflows/wiki_synthesis/runner.py::invoke_wiki_synthesis`** — the canonical invocation pattern. Bundles PostgresSaver checkpointer, `thread_id="wiki_synthesis__{item_id}"`, the Langfuse callback at `graph.invoke` level (not just per-LLM-call), and `langfuse_session_id` + tags metadata so retries/replays of the same item group into one Langfuse session view. Callers (Dagster asset in PR 3, future CLI, ad-hoc scripts) should prefer this over compiling the graph manually. Three new tests cover end-to-end invocation, callback propagation when `LANGFUSE_PUBLIC_KEY` is set, and silent omission when unset.
- **PR 2 new-capability test suite** under `tests/wiki_synthesis/`. Verifies the properties that justify using LangGraph at all:
  - **Replay correctness** (`test_replay.py`) — when `commit` raises mid-txn, the post-fan-out checkpoint is preserved; resuming via `graph.invoke(None, config)` re-runs only `commit`. The synthesis LLM is **not** re-called (the architecturally important property — no LLM cost on commit-time DB failure). Note: the original "per-entity replay" claim was overstated; Send fan-out is one atomic super-step from the checkpointer's perspective. Documented in the test docstring.
  - **Send-API parallelism** (`test_parallelism.py`) — N synthesis calls routed through `threading.Barrier(N, timeout)`; barrier only releases when all N threads arrive. If Send were serialized, only one thread would arrive and the test fails fast.
  - **Commit txn atomicity** (`test_commit_atomicity.py`) — patching `insert_processed` or `upsert_page` to raise mid-txn aborts the whole transaction; `wiki.pages`, `wiki.aliases`, `wiki.processed` all stay empty. The `.md` files DO exist on disk (`write_page` is file-atomic, outside the txn boundary) — pinned in the test as intentional.
  - **Concurrent alias safety** (`test_alias_concurrency.py`) — 10 threads in two groups of 5 race for the same two aliases via separate psycopg connections; `ON CONFLICT (alias) DO NOTHING` lands exactly one row per alias, no deadlock, no exception.
- **`runner.py` auto-resume** — `invoke_wiki_synthesis` now checks `graph.get_state(config).next`. If the thread is paused mid-execution (prior invocation raised before END), it invokes with `None` to resume. Otherwise (non-existent thread or successfully ended thread) it invokes with the input state for a fresh run. Critical because `invoke(state, config)` on an existing thread restarts from START — silently re-firing every entity LLM call. The completed-thread re-run case is intentional (Dagster re-materializing should re-run with current inputs) and pinned by `test_runner_re_runs_on_completed_thread`.
- **`pytest-timeout>=2.3,<3.0`** added to dev deps so threading-based tests use `@pytest.mark.timeout(N)` and never hang the suite.
- **Shared test helpers** at `tests/wiki_synthesis/_helpers.py` (`make_item`, `make_extraction`, `build_synthesis_output`, `extract_entity_id_from_prompt`) — extracted from the parity tests so the new-capability tests don't duplicate factories.

#### PR 3 — rewire and cleanup

- **`wiki_synthesized` Dagster asset** is now dynamic-partitioned (`DynamicPartitionsDefinition(name="wiki_items")`) and invokes `invoke_wiki_synthesis(item, db_url, wiki_dir)` per partition. Per-partition retries auto-resume from the LangGraph checkpoint via the runner's auto-resume heuristic — no LLM re-calls if the prior failure was in commit.
- **`wiki_pending`** becomes a discovery asset: it scans raw_store for items not in `wiki.processed` (status=ok or skipped) and registers them as new `wiki_items` partitions on the Dagster instance. Idempotent re-runs only add what isn't already a partition.
- **`wiki_index_updated`** reads from `wiki.pages` (Postgres) via `domains.wiki.state.get_all_pages`. Intentionally NOT declared as `deps=[wiki_synthesized]` because the default `AllPartitionMapping` would block the index on every partition completing — never true with dynamic partitions. Phase E will add a sensor; for now, materialize manually.
- **`WikiResource`** gains a `database_url` field that resolves at call time via `get_database_url()` (Dagster idiom — pass `dg.EnvVar("DATABASE_URL")` at construction or rely on env-var fallback). Removed `state_db_path` and `aliases_path` (legacy SQLite/YAML).
- **`domains.store.get_content_by_id(content_id, *, db_path)`** — focused single-row lookup the partitioned asset needs (vs. loading every row via `get_contents`).
- **`domains.wiki.sources.RawStoreSource.get_item(item_id)`** — convenience wrapper on top of `get_content_by_id`.
- **Deleted**:
  - `packages/workflows/src/workflows/wiki/{__init__,ingest,state}.py` and the now-empty `wiki/` folder.
  - `tests/wiki/test_ingest.py`, `tests/wiki/test_state.py` — the parity tests for the deleted code, now redundant with `tests/wiki_synthesis/`.
- **Moved**: `workflows/wiki/prompts.py` → `workflows/wiki_synthesis/prompts.py` (still in use by `entity_graph.py` and `nodes.py`; imports rewired).

---

## [0.4.0] — 2026-05-01

Phase A foundation release — restructure into a uv workspace with Postgres infrastructure ready for the LangGraph wiki workflow (Phase B). No behavior change; 90/90 tests pass.

- **uv workspace with 5 packages** under `packages/{domains,workflows,retrievers,evals,orchestrators}/`. Cross-package import discipline enforced via `pyproject.toml` deps:
  - `knowledge-domains` — pure data layer (pydantic, psycopg, pyyaml, python-frontmatter); no LLM/ML/Dagster deps.
  - `knowledge-workflows` — depends on domains + retrievers; adds langgraph, langgraph-checkpoint-postgres, langchain-openai, langchain-anthropic, langfuse.
  - `knowledge-retrievers` — depends on domains; adds chromadb, sentence-transformers, rank-bm25, langchain-text-splitters, langchain-experimental, langchain-community, tiktoken.
  - `knowledge-evals` — depends on domains + workflows + retrievers; adds ragas.
  - `knowledge-orchestrators` — depends on all four others; the only package allowed to depend on Dagster, dagster-postgres, dagster-webserver, dagster-dg-cli, poethepoet.
- **Code migrated into the 5 packages** — the old `src/knowledge_pipeline/` tree split across the new packages and deleted:
  - `domains/`: `store.py`, `wiki/{types,io,aliases,sources}.py`, `wiki/schema/wiki.sql`
  - `retrievers/`: `chunking/`, `postprocess/`, `retrieval/` (cosine, hybrid, rerank, fusion, registry), `vector_store/chroma.py`
  - `workflows/`: `llm.py`, `wiki/{prompts,state,ingest}.py`, `agents/nodes/query_rewrite.py` (was `lib/retrieval/hyde.py`)
  - `evals/`: `rag.py` (was `lib/eval.py`)
  - `orchestrators/`: `definitions.py`, `config.py`, `strategies.{py,yaml}` (the `.py` was `lib/utils.py`), and the entire `defs/` tree (`shared/`, `workbench/`, `pipelines/`).
- **Boundary fixes** — `domains.store` and `retrievers.vector_store.chroma` no longer import `orchestrators.config`. Path arguments (`db_path`, `chroma_path`) are now passed in by the caller. `db_path` is keyword-only across all `domains.store` functions. `HyDE` retrieval has an LLM dep so it lives in `workflows.agents.nodes.query_rewrite`, not in `retrievers`.
- **Workspace package pip-names prefixed with `knowledge-`** — matches the `newsletter-assistant` workspace pattern: prefix lives only in `pyproject.toml` `dependencies` and `[tool.uv.sources]` keys; import paths stay plain (`from domains import …`). Pre-empts cross-project name collisions when `newsletter-assistant` consumes `knowledge-workflows`.
- **Root project shape** — `pyproject.toml` is now a virtual project: `[project].dependencies` lists all 5 prefixed members, no `[build-system]`, no `[tool.hatch.*]`. Bare `uv sync` (without `--all-packages`) installs the full workspace.
- **Postgres `knowledge_pipeline` database** — idempotent `docker/postgres/init/01-create-knowledge-db.sh` mounted into the existing Dagster postgres service via `/docker-entrypoint-initdb.d` (single instance, no second container).
- **`packages/domains/src/domains/wiki/schema/wiki.sql`** — Postgres schema for wiki state: `wiki.processed` (PK `(item_id, source_type)`), `wiki.pages` (PK `entity_id`, jsonb columns for `related`/`sources`/`source_types`), `wiki.aliases` (UNIQUE `alias`, indexed by `entity_id`). Ready for Phase B.
- **Dagster code locations preserved as workbench / pipelines** — `poe dev` loads both `orchestrators.defs.workbench.definitions` and `orchestrators.defs.pipelines.definitions` as separate code locations (matches the original split). `orchestrators.definitions` is the merger used by Docker/prod (single `-m` flag in `configs/workspace.yaml` + `docker/code/Dockerfile`). `[tool.dagster].module_name` points at the merger; `code_location_name` is `orchestrators`.
- **CLI migrated from `dagster job execute` to `dg launch`** — Dagster 1.13 deprecated the former. `poe index/backup/eval` now use `dg launch -m … --job …`. Added `dagster-dg-cli` to the orchestrators deps.
- **Dev port moved from 3000 to 3030** — `poe dev`, `poe tunnel` (host side), `docker-compose` host port mapping, README references. Container internal port and remote production port stay at 3000.
- **Docker code-server image** rebuilt for the workspace layout — copy each member's `pyproject.toml` first for layer-cached deps resolution, then per-member `src/` for editable installs. Switched from `pip install uv` to a binary copy from `ghcr.io/astral-sh/uv:latest` (~150s faster). Use `--no-install-workspace --package knowledge-orchestrators` so only the deployable's dep tree is resolved.
- **New poe tasks** (stubs for Phase B) — `wiki-ingest`, `wiki-lint`, `research`, `wiki-eval`, `rag-eval`, `reset-wiki`, `reset-checkpoints`, `reset-everything`.
- **Removed** `src/knowledge_pipeline/` — the entire old source tree.

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
