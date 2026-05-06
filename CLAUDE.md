# CLAUDE.md

Guidance for Claude Code working in this repo.

## Project Overview

Dagster-orchestrated knowledge pipeline; companion to `newsletter-assistant`. See [`CHANGELOG.md`](CHANGELOG.md) for release history.

## Git Workflow

Main is **protected**. All changes go through feature branches and pull requests.

## Package Manager

[`uv`](https://docs.astral.sh/uv/) with **uv workspaces**. No `pip` or `poetry`.

### Workspace Members

| Package | Path | Description |
|---|---|---|
| `knowledge-domains` | `packages/domains` | Pure data layer — types, schema, sources (no LLM/Dagster deps) |
| `knowledge-workflows` | `packages/workflows` | LangGraph workflows + agent primitives (wiki synthesis, research) |
| `knowledge-retrievers` | `packages/retrievers` | RAG infra — chunking, embedding, vector store, retrieval strategies |
| `knowledge-evals` | `packages/evals` | Eval harnesses — RAG metrics (Ragas), wiki quality dimensions |
| `knowledge-orchestrators` | `packages/orchestrators` | Dagster definitions — the **only** package allowed to depend on Dagster |

**Dependency rule:** `domains` is the foundation, no internal imports. `workflows` and `retrievers` depend on `domains`. `evals` depends on `domains` + `retrievers`. `orchestrators` is the top — depends on `domains` + `workflows` for production; `retrievers` + `evals` are optional extras (`[workbench]`) for the local RAG workbench. Nothing outside `orchestrators` may `import dagster`.

## Common Commands (via `poe`)

```bash
uv run poe check       # fmt, lint, tests
uv run poe test        # pytest -v
uv run poe fix         # fmt-fix + lint-fix
```

Tests live at the repo root (`tests/`), not inside packages — fixtures crossing package boundaries (e.g. `wiki_pg` for Postgres) need a single shared root.

## DAG Versioning Pattern

Each Dagster pipeline gets a `<NAME>_DAG_VERSION = "1"` constant in `packages/orchestrators/src/orchestrators/config.py`, applied as `code_version=` on every asset in that pipeline. Bump manually only when that pipeline's DAG logic changes — independent per-pipeline staleness signal, decoupled from package version (which the version-bump workflow rolls forward on every release).

Naming: `BACKUP_<DOMAIN>_DAG_VERSION` for backup pipelines (groups them alphabetically); `<DOMAIN>_DAG_VERSION` for everything else.

## Architecture

```
packages/
  domains/         # Pure data layer (no LLM/Dagster deps)
  workflows/       # LangGraph workflows + agents (wiki synthesis, research)
  retrievers/      # RAG infra — workbench only
  evals/           # Ragas RAG eval + wiki eval — workbench only
  orchestrators/   # Dagster definitions — only package that imports dagster
    defs/
      shared/      # Shared resources (raw_store, chroma, etc.)
      workbench/   # Manually-triggered jobs (index strategies, eval)
      pipelines/   # Scheduled production (backup_readings, wiki)

datasets/          # Pinned eval datasets — checked in
data/              # Runtime, gitignored — raw_store.db, chroma/, wiki/, eval_results/
backups/           # Backup pipeline landing — gitignored
.rclone/           # rclone.conf, mounted into dagster-code — gitignored
```

**Key entry points:**
- `orchestrators.definitions:defs` — full code location for `dagster dev`
- `orchestrators.defs.workbench.definitions:defs` — workbench-only
- `orchestrators.defs.pipelines.definitions:defs` — pipelines-only (production)

## Testing Strategy

- Unit tests in `tests/` with mocked LLMs and `wiki_pg` Postgres fixture for state tests.
- Sandbox materializations for Dagster assets — see `packages/orchestrators/src/orchestrators/defs/pipelines/backup_readings/README.md` ("Layer 2").
- LLM calls in tests **must** be mocked (use `unittest.mock.patch` at the **import** location, not the source location).
- Postgres tests use `pytest-postgresql` with the `wiki_pg` fixture (fresh psycopg connection to a temp DB with `wiki.sql` loaded).

## Deployment

Hetzner Cloud VM, Docker Compose. Entry: `scripts/deploy-hcloud.sh` (`setup`, `deploy`, `push-creds`).

Three Dagster containers + Postgres:
- `dagster-code` runs the user-code gRPC server (custom image, uv-built).
- `dagster-webserver` and `dagster-daemon` share a slim image with pinned `dagster*` packages.
- All connect to one Postgres for run history + LangGraph checkpoints + wiki state.

`push-creds` rsyncs `~/.config/rclone/rclone.conf` from laptop to server; compose bind-mounts it read-only into the container at `/home/dagster/.config/rclone/` — rclone's default lookup path under `$HOME=/home/dagster`.

## Backup Pipeline (`backup_readings`)

Daily-partitioned. See `packages/orchestrators/src/orchestrators/defs/pipelines/backup_readings/README.md` for the DAG diagram and full runbook (rclone setup, healthchecks.io, restore).

Per-host config is env-driven (`BACKUP_SOURCE_DIR`, `BACKUP_DIR`, `DRIVE_REMOTE`, `HEALTHCHECK_PING_URL`) — all optional with short-circuit behaviour for laptop dev. In Docker Compose, `BACKUP_SOURCE_DIR` is the host path; compose bind-mounts it to `/app/source` and overrides the in-container env var.
