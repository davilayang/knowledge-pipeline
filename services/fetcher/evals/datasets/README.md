# `services/fetcher/evals/` datasets

Pinned fixture manifests for the fetcher's fidelity evals. Each section records
a **contract** — what the file identifies and what a consumer may assume — not
the file's contents. Counts, fixture tables, and per-fixture scores are
derivable from the JSONL and from a run; they go stale in prose and are
deliberately absent here.

## `structure_fidelity_fixtures.jsonl`

**Consumed by** `evals/structure_fidelity.py`, via `--fixtures` (this file is
the default).

**Bodies are not in this repo.** The fixtures are verbatim third-party articles
and `knowledge-pipeline` is public, so committing them would republish them.
Each row instead pins the identity of a row in a `queue.db`, and the harness
reads the body from the `--queue-db` given at run time.

**Row shape.** One JSON object per line:

| field | contract |
|---|---|
| `notion_page_id` | Primary key into `queue_items`; the row whose `raw_content_override` is the fixture body. |
| `url` | The article's source URL. Display only — never used for lookup. |
| `source_chars` | Length of the body when pinned. Display only. |
| `source_sha256` | SHA-256 of the body when pinned. **Verified on every load.** |

**Failure is loud, by design.** A row that has been deleted, or whose body no
longer hashes to `source_sha256`, raises rather than being skipped — a silently
dropped or silently mutated fixture would move a score with no visible cause.

**Reproducibility limit.** This binds the eval to a machine that has the
matching `queue.db`. It cannot run in CI, and a `queue.db` that loses these rows
takes the fixtures with it. Committed synthetic fixtures covering the same
failure shapes — code-heavy bodies, list-shaped articles, chrome-heavy sources —
would remove that limit and are not yet written.

**Selection.** Every `raw_content_override` row in production `queue.db` over
2,000 characters at the time of pinning — a population, not a sample. It is
small, and skewed short: re-pin from a larger corpus before treating a corpus
mean from it as a robust estimate.
