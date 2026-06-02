# CLAUDE.md

Guidance for Claude Code working in this repo.

## Project Overview

@/Users/cyyang/GitHub/data-context-builder/documents/personal-knowledge-os/framing.md

@/Users/cyyang/GitHub/data-context-builder/documents/personal-knowledge-os/trajectories/knowledge-pipeline.md

Two `@`-imports above (both into the personal-knowledge-OS hub):

- **OS-wide framing (canonical):** `~/GitHub/data-context-builder/documents/personal-knowledge-os/framing.md`
  — system overview, exit ramps, thinking-partner front door, decision
  rubric. Shared across both repos in the personal-knowledge-OS. Edit
  there when the OS-wide concept evolves.
- **This repo's per-repo trajectory:** `~/GitHub/data-context-builder/documents/personal-knowledge-os/trajectories/knowledge-pipeline.md`
  — knowledge-pipeline's role, what this repo produces over time,
  current journey state, cross-repo split. Edit there when this repo's
  status changes — trajectory PRs land in the hub, derived from this
  repo's `CHANGELOG.md`.

For the OS-wide system architecture (data flow, cross-repo split,
depth-signal taxonomy, architectural rules, compounding loop, wave
sequencing across repos): see
`~/GitHub/data-context-builder/documents/personal-knowledge-os/architecture.md`.
Loaded on demand, not auto-imported.

## Decision rubric for future work

When evaluating a PR, feature, pipeline change, or architectural decision, ask:

1. **Does it move us toward thinking-partner depth?** (Better recall quality,
   more useful entity wiki pages, sharper cross-content connection — all feed
   back into newsletter-assistant's ability to be a thinking partner)
2. **Does it serve at least one exit ramp?** (Application: smoother bridges
   from corpus to research panel. Retention: better wiki pages, better
   indexing, better retrieval, better cross-corpus joins.)
3. **Does it strengthen the second brain over time?** (This repo IS most of
   the second brain — wikis, indices, retrieval evaluation. Most work here
   will pass this test by default; the sharper question is *which* part of
   the second brain it strengthens, and whether it closes a current gap.)
4. **Does it serve the personal-corpus-of-one shape?** (vs. generic
   multi-tenant pipeline patterns — this is one user's reading life, not a
   SaaS. Don't build for hypothetical scale.)

Work that doesn't pass any of these probably isn't worth doing right now.
Work that strengthens an underbuilt or in-flight piece (per the trajectory
above) beats work that polishes the already-working layer.

## Second opinions on non-trivial decisions

Before locking in an architecture choice, pipeline / DAG topology change, schema migration, embedding-model or chunking-strategy swap, or refactor with non-obvious tradeoffs, invoke an advisor agent — don't ship the recommendation on a single model's read.

- Use **`codex-advisor`** for bounded critique of a specific design, snippet, or plan (Codex runs in a read-only sandbox and returns one tight judgment).
- Use **`gemini-advisor`** for open-ended exploration or when current web context matters ("is X still considered best practice?", "what are people doing with Y now?").

Pass absolute file paths in the prompt — both agents read files themselves; no need to paste contents. Surface the response verbatim to the user before acting on it; do **not** filter or paraphrase the second opinion away.

Both agents live under `.claude/agents/` and are symlinked from `~/GitHub/data-context-builder/claude-agents/`.

## Git Workflow

Main is **protected**. All changes go through feature branches and pull requests.

## Package Manager

[`uv`](https://docs.astral.sh/uv/) with **uv workspaces**. No `pip` or `poetry`.

### Workspace Members

| Package | Path | Description |
|---|---|---|
| `knowledge-domains` | `packages/domains` | Pure data layer — types, schema, sources (no LLM/Dagster deps) |
| `knowledge-workflows` | `packages/workflows` | LangGraph workflows + agent primitives (wiki synthesis, research) |
| `knowledge-retrievers` | `packages/retrievers` | RAG infra — chunking, embedding (OpenAI), Chroma HTTP client, retrieval protocols |
| `knowledge-evals` | `packages/evals` | Eval harnesses — retrieval (Recall@K / MRR / nDCG), generation quality (faithfulness / relevance / grounding, reserved), wiki dimensions (reserved) |
| `knowledge-orchestrators` | `packages/orchestrators` | Dagster definitions — the **only** package allowed to depend on Dagster |

**Dependency rule:** `domains` is the foundation, no internal imports. `workflows` and `retrievers` depend on `domains`. `evals` depends on `domains` + `retrievers`. `orchestrators` is the top — depends on everything below. Nothing outside `orchestrators` may `import dagster`.

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
  retrievers/      # RAG infra — chunking, OpenAI embedding, Chroma HTTP client, retrieval protocols
  evals/           # Retrieval eval (active), generation + wiki eval (reserved)
    datasets/      # Pinned eval JSONL datasets — checked in
  orchestrators/   # Dagster definitions — only package that imports dagster
    defs/
      shared/                  # Shared resources (raw_store, chroma, etc.) + partitions.py
      backup_readings/
      triage_queued_items/
        classify.py            # Pure URL → Content Type / canonicalize (no I/O)
        url_meta.py            # Best-effort HTTP fetch → page title + description
      extract_complex_contents/
        extractors/            # Extractor strategies (mirrors fetchers/)
        fetchers/
      synthesize_wiki/
      populate_vector_store/
      workbench/               # Retrieval strategy variants for eval (idx_*, evaluate)
      upstream_sources.py

configs/           # Dagster config — dagster.yaml, workspace.yaml
docker/            # Dockerfiles — code/, dagster/, postgres/ subdirs
scripts/           # Deployment scripts — deploy-hcloud.sh
tests/             # Root-level pytest suite (shared fixtures crossing package boundaries)
datasets/          # Pinned eval datasets — checked in
notebooks/         # Exploratory notebooks (Phase C / debugging)
data/              # Runtime, gitignored — raw_store.db, chroma/, wiki/, eval_results/
backups/           # Backup pipeline landing — gitignored
.rclone/           # rclone.conf, mounted into dagster-code — gitignored
ai-findings/       # Investigation/discovery notes written by Claude Code sessions
ai-plannings/      # Implementation plans written by Claude Code sessions
```

**Key entry points:**
- `orchestrators.definitions:defs` — Dagster code location (loaded by `dagster dev` and the production gRPC server)

## Testing Strategy

- Unit tests in `tests/` with mocked LLMs and `wiki_pg` Postgres fixture for state tests.
- Sandbox materializations for Dagster assets — see `packages/orchestrators/src/orchestrators/defs/backup_readings/README.md` ("Layer 2").
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

Daily-partitioned. See `packages/orchestrators/src/orchestrators/defs/backup_readings/README.md` for the DAG diagram and full runbook (rclone setup, healthchecks.io, restore).

Per-host config is env-driven via required `dg.EnvVar` (`BACKUP_SOURCE_DIR`, `BACKUP_DIR`, `DRIVE_REMOTE`, `DRIVE_BACKUP_ROOT`, `HEALTHCHECK_PING_URL`) — unset → run init fails fast. Set in deploy `.env` or shell profile. In Docker Compose, `BACKUP_SOURCE_DIR` is the host path; compose bind-mounts it to `/app/source` and overrides the in-container env var to that fixed path.
