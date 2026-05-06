# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Dagster-orchestrated knowledge pipeline. Companion repo to `newsletter-assistant`:

- **Phase 1** — RAG over articles (ChromaDB, multiple chunking/embedding strategies, Ragas eval).
- **Phase 2** — LangGraph wiki synthesis: extract entities from articles, synthesise per-entity wiki pages, store in Postgres + markdown files.
- **Phase 3 (this branch)** — Backup pipeline for the source newsletter-assistant SQLite DBs (snapshot → Drive offload → retention prune → healthchecks.io ping).

## Changelog

See [`CHANGELOG.md`](CHANGELOG.md) for release history. Each PR should add its entry under `[Unreleased]`.

## Git Workflow

Main is **protected**. All changes go through feature branches and pull requests. Never commit directly to main.

## Package Manager

[`uv`](https://docs.astral.sh/uv/) with **uv workspaces**. No `pip` or `poetry`.

```bash
uv sync                       # install all workspace members + dev deps
uv run <command>              # run inside the project venv
uv run --package <name> ...   # run inside a specific workspace member
```

### Workspace Members

| Package | Path | Description |
|---|---|---|
| `knowledge-domains` | `packages/domains` | Pure data layer — types, schema, sources (no LLM/Dagster deps) |
| `knowledge-workflows` | `packages/workflows` | LangGraph workflows + agent primitives (wiki synthesis, research) |
| `knowledge-retrievers` | `packages/retrievers` | RAG infra — chunking, embedding, vector store, retrieval strategies |
| `knowledge-evals` | `packages/evals` | Eval harnesses — RAG metrics (Ragas), wiki quality dimensions |
| `knowledge-orchestrators` | `packages/orchestrators` | Dagster definitions — the **only** package allowed to depend on Dagster |

**Dependency rule:** `domains` is the foundation, no internal imports. `workflows` and `retrievers` depend on `domains`. `evals` depends on `domains` + `retrievers` (for index strategies). `orchestrators` is the top — depends on `domains` + `workflows` for production; `retrievers` + `evals` are optional extras (`[workbench]`) installed only for local RAG workbench use. Everything Dagster-flavoured lives in `orchestrators`. Nothing else may `import dagster`.

## Common Commands (via `poe`)

```bash
uv run poe check       # fmt, lint, tests
uv run poe test        # pytest -v
uv run poe fmt-fix     # auto-format with black
uv run poe lint-fix    # auto-fix linting with ruff
uv run poe fix         # fmt-fix + lint-fix
```

Single test file:
```bash
uv run pytest tests/path/to/test_foo.py -v
```

Tests live at the repo root (`tests/`), not inside packages — fixtures crossing package boundaries (e.g. `wiki_pg` for Postgres) need a single shared root.

## DAG Versioning Pattern

Each Dagster pipeline gets a `<NAME>_DAG_VERSION = "1"` constant in `packages/orchestrators/src/orchestrators/config.py`, applied as `code_version=` on every asset in that pipeline. Bump manually only when that pipeline's DAG logic changes — independent per-pipeline staleness signal, decoupled from package version (which the version-bump workflow rolls forward on every release).

```python
# config.py
BACKUP_READINGS_DAG_VERSION = "1"
# BACKUP_WIKI_DAG_VERSION = "1"      # future — wiki PG backup
# BACKUP_DAGSTER_DAG_VERSION = "1"   # future — Dagster metadata PG backup
# WIKI_DAG_VERSION = "1"             # wiki synthesis pipeline (when adopted)
```

Naming: `BACKUP_<DOMAIN>_DAG_VERSION` for backup pipelines (groups them alphabetically); `<DOMAIN>_DAG_VERSION` for everything else.

## Architecture

```
packages/
  domains/src/domains/
    store.py              # Read-only SQLite access to raw_store.db
    wiki/                 # Wiki domain types + Postgres schema/helpers
      sources.py          # RawStoreSource — reads pending articles
      state.py            # Postgres helpers (insert_processed, upsert_page, etc.)
      types.py            # WikiPage, Entity, AliasStore

  workflows/src/workflows/
    llm.py                # LLM wrappers — generate, generate_structured (Langfuse-aware)
    shared/               # Cross-workflow plumbing
      checkpointer.py     # PostgresSaver context manager for LangGraph
      observability.py    # Langfuse callback factory
    wiki_synthesis/       # LangGraph workflow (replaces legacy wiki/ingest.py)
      graph.py            # Parent StateGraph — extract → Send fan-out → commit
      entity_graph.py     # Per-entity sub-graph (one node: process_entity)
      nodes.py            # extract_entities, commit (transactional)
      parsing.py          # Pure helpers — parse_llm_page_output, slug_from_id
      runner.py           # invoke_wiki_synthesis() — canonical entry point
    agents/               # Multi-agent helpers (research panel)

  retrievers/src/retrievers/
    chunking/             # Markdown-aware, recursive, semantic chunkers
    vector_store/         # ChromaDB ops — embed, search, upsert
    retrieval/            # Retrieval strategies (hybrid, rerank)
    postprocess/          # Result post-processing (dedup, citation extraction)

  evals/src/evals/
    rag.py                # Ragas-based RAG eval harness
    cli/                  # CLIs: rag_eval, wiki_eval

  orchestrators/src/orchestrators/
    config.py             # Paths, env-driven knobs, DAG version constants
    definitions.py        # Top-level entrypoint — merges workbench + pipelines
    strategies.py         # Index strategy registry (markdown_minilm, etc.)
    strategies.yaml       # Per-strategy configuration
    defs/
      shared/             # Shared Dagster resources (raw_store, chroma, etc.)
      workbench/          # Manually-triggered jobs (index strategies, eval)
        idx_markdown_minilm/, idx_markdown_bge/, idx_recursive_minilm/, idx_semantic_minilm/
        evaluate/         # RAG eval job
        definitions.py    # Code location merging the four index strategies + eval
      pipelines/          # Scheduled production jobs
        backup_readings/  # Daily SQLite backup with Drive offload + healthchecks
        wiki/             # Dynamic-partitioned wiki synthesis (per article)
        definitions.py    # Code location merging backup + wiki

datasets/                 # Pinned eval datasets — checked in
data/                     # Runtime — gitignored
  raw_store.db            # Local copy of newsletter-assistant raw_store
  chroma/                 # ChromaDB vector index
  wiki/                   # Synthesised wiki pages (markdown)
  eval_results/           # JSON output from eval runs

backups/                  # Local snapshot landing for backup_readings — gitignored
.rclone/                  # rclone.conf, mounted into dagster-code container — gitignored
```

**Key entry points:**
- `orchestrators.definitions:defs` — the full Dagster code location loaded by `dagster dev`.
- `orchestrators.defs.workbench.definitions:defs` — workbench-only (manual jobs).
- `orchestrators.defs.pipelines.definitions:defs` — pipelines-only (scheduled production jobs).

## Testing Strategy

- **Unit tests** in `tests/` — fixtures, mocked LLMs, `wiki_pg` Postgres fixture for state tests.
- **Sandbox materializations** for Dagster assets — drop a tempdir + fake source into env, run `dg.materialize(...)` directly. See `packages/orchestrators/src/orchestrators/defs/pipelines/backup_readings/README.md` ("Layer 2") for the pattern.
- **Real-data validation** — manual; pointed at `~/GitHub/newsletter-assistant/data/` on laptop.
- LLM calls in tests **must** be mocked (use `unittest.mock.patch` at the import location, not the source location).
- Postgres tests use `pytest-postgresql` with the `wiki_pg` fixture (yields a fresh psycopg connection to a temp DB with `wiki.sql` loaded).

## Data Files

Gitignored — back up `data/*.db`:
- `data/raw_store.db` — Article content, copied from newsletter-assistant
- `data/chroma/` — ChromaDB vector index (rebuildable from raw_store.db)
- `data/wiki/` — Synthesised wiki pages (markdown, rebuildable from articles)

## Deployment

Server: Hetzner Cloud VM, Docker Compose. See `scripts/deploy-hcloud.sh`:
- `setup` — one-time provisioning (clone repo, sync `.env`, mkdir data/datasets dirs).
- `deploy [--no-build]` — git pull, rebuild images, restart services.
- `push-creds` — rsync `~/.config/rclone/rclone.conf` from laptop to server (mounted read-only into the dagster-code container at `/root/.config/rclone`).

The `dagster-code` container runs the user code gRPC server. The `dagster-webserver` and `dagster-daemon` containers are stock Dagster images. All three connect to a shared `postgres` service for run history + asset materialization tracking + LangGraph checkpoints + wiki state.

## Backup Pipeline (`backup_readings`)

The `backup_readings/` module under `defs/pipelines/` is daily-partitioned:

```
snapshot_raw_store ─┐
                    ├─→ verify_* (blocking) ─→ check_drive_capacity → upload_snapshots_to_drive ─┐
snapshot_sessions  ─┘                                                                              │
                                                            ┌──────────────────────────────────────┤
                                                            ▼                                      ▼
                                                 prune_drive_backups                  prune_local_backups

  on job SUCCESS  ──→  ping_healthcheck_on_success (run-status sensor)
```

Asset keys are namespaced (`snapshots/raw_store`, `google_drive/storage_capacity`, `local_disk/pruned_old_backups`) — Dagster UI renders only the leaf, so leaves are deliberately self-explanatory.

Tunables live in `defs/pipelines/backup_readings/def_config.py` (retention, capacity threshold, cron, pipeline tag). Per-host config is env-driven (`BACKUP_SOURCE_DIR`, `BACKUP_DIR`, `DRIVE_REMOTE`, `HEALTHCHECK_PING_URL`) — all optional with short-circuit behaviour for laptop dev.

See `defs/pipelines/backup_readings/README.md` for the full runbook (rclone setup, healthchecks.io setup, restore, capacity-over-threshold response).
