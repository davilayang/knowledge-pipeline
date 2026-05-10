# Notebooks

Exploratory and operational notebooks. Not production code — no scheduled runs, no CI imports.

## Launch

```bash
uv run poe notebooks
# (equivalent to: uv run --extra notebooks jupyter lab notebooks/ --port 8888 \
#   --IdentityProvider.token local-jupyter-dev-token --no-browser)
```

The `notebooks` extra installs `jupyterlab`, `pandas`, `matplotlib`, `ipykernel`, plus `jupyter-collaboration` and `jupyter-mcp-tools` (the server-side companions for the MCP integration below). Opt-in — prod installs stay slim.

## Jupyter MCP server (Claude Code integration, optional)

A local `.mcp.json` at the repo root can register a `jupyter` MCP server that lets Claude Code introspect a running notebook session — read cells, execute code, edit, etc. The file is **gitignored** (per-developer config, not team-shared); copy-paste the template below into `.mcp.json` at the repo root if you want it.

```json
{
  "mcpServers": {
    "jupyter": {
      "command": "uvx",
      "args": ["jupyter-mcp-server@latest"],
      "env": {
        "JUPYTER_URL": "http://localhost:8888",
        "JUPYTER_TOKEN": "local-jupyter-dev-token",
        "ALLOW_IMG_OUTPUT": "true"
      }
    }
  }
}
```

To use it:

1. Drop the JSON above into `.mcp.json` at the repo root (gitignored).
2. Run `uv run poe notebooks` in a terminal — starts JupyterLab on port 8888 with the matching token.
3. Open Claude Code in the repo. First time, it'll prompt to approve the project's `.mcp.json` (`jupyter` server entry).
4. Approve. Claude Code can now invoke MCP tools against the running server.

The token is a **localhost-only dev string**, not a secret. Don't expose port 8888 publicly. The MCP server itself is launched via `uvx jupyter-mcp-server@latest` — no extra install needed.

## Inventory

| Notebook | Purpose |
|---|---|
| `01_explore_sources.ipynb` | Read-only scan of `raw_store` / `sessions.db` / `notes/` / `research.db` — counts, sample items, content-length distributions |
| `02_build_retrieval_eval_set.ipynb` | Phase C: bootstrap `datasets/retrieval_eval.jsonl` — summarize items → generate candidate queries → n-gram-overlap reject → hand-curate → write JSONL |
| `03_compare_eval_runs.ipynb` | Glob `data/eval_results/retrieval_*.json`, join on `(model, chunker)`, plot per-source metric bars, highlight winners |
| `04_inspect_retrieval_misses.ipynb` | For a query that scored Recall@5 = 0, pull the actual top-K chunks and the expected document's chunks side-by-side |

## Conventions

- **No long-running cells in committed state.** Run, capture insight, clear outputs (`Edit → Clear All Outputs`) before committing if outputs include large data.
- **Read source DBs read-only.** None of these notebooks should mutate `raw_store.db`, `sessions.db`, `research.db`, or `notes/`. The Phase A `*Source` classes are read-only by construction.
- **Eval results live in `data/eval_results/`.** Notebook 03 reads from there; never writes a new result JSON (that's the `eval-retrieval` CLI's job).
- **Datasets land in `datasets/`.** Notebook 02 writes the curated eval set to `datasets/retrieval_eval.jsonl`; commit the file, not the cells that built it.

## Why notebooks at all

Phase B's harness is a clean Python module with a CLI; you can run it from a terminal forever. But the *adjacent* work — building the eval dataset (notebook 02), comparing runs (03), debugging miss patterns (04), and getting a feel for what's in each source (01) — is iterative, plot-heavy, and inherently exploratory. Wiring all of that as production scripts would either bloat the package or live in scratch directories on someone's laptop. Checked-in notebooks split the difference: discoverable, reproducible-enough, and clearly marked as exploratory.
