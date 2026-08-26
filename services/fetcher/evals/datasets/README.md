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

**Two lanes.** `lane` selects where the body comes from and what is run against it.

| field | lane | contract |
|---|---|---|
| `lane` | both | `article` or `transcript`. Decides the loader, the prompt, and the chain. |
| `url` | both | Source URL. Display only — never used for lookup. |
| `source_chars` | both | Length of the body when pinned. Display only. |
| `source_sha256` | both | SHA-256 of the body when pinned. **Verified on every load.** |
| `notion_page_id` | article | Primary key into `queue_items`; the row whose `raw_content_override` is the body. Needs `--queue-db`. |
| `url_hash` | transcript | Primary key into `fetches.db`'s `cache`; the body is rebuilt from that row's caption chunks. Needs `--fetches-db`. |
| `recall_floor` | transcript | Minimum acceptable trigram recall. Below it, the run reports `!! BELOW FLOOR`. |
| `known_failing` | any | Optional note shown beside a red verdict, marking a fixture that reproduces an open bug rather than a regression. Remove it in the commit that makes the fixture pass. |

**The two transcript fixtures.** `EnsZazeC1h4` (109,064 chars) structures faithfully
whether chunked or not, and guards against regression. `Ybrl4FYM57c` (111,640 chars)
was pinned while it reproduced the transcript collapse — 19-41% retained across four
runs — and is the acceptance case for chunking, which took it to 93%.

**Why a transcript fixture is here at all.** Nothing in this repo changes the
transcript structurer. It is pinned because it is the **control case** for the
article result: the same model, on one call, holds fidelity at over 100,000
characters. That is the evidence for reading the article lane's loss as a prompt
problem rather than a length problem, and for not chunking long inputs. If this
fixture ever falls below its floor, both of those conclusions need revisiting —
so it is a regression guard, not an A/B arm, and it is scored against a floor
rather than against a second prompt.

**Article rows are more durable than transcript rows.** `queue_items` rows never
expire. Cache rows carry a TTL and are deleted on the first lookup after they
expire, so the transcript fixture will eventually become unloadable and will say
so rather than silently vanishing from the run.

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
