# searcher

`POST /v1/search` for newsletter-assistant's voice `recall` and MCP
`search_knowledge`. The response shape, status rules and versioning are the
hub contract `documents/knowledge-os/contracts/search.md` in
`davilayang/data-context-builder`; change the contract before this service.

## State

Every request runs through the engine (`src/searcher/engine.py`): the requested
lanes run concurrently on one shared query embedding, each reports `ok`,
`failed`, `timed_out` or `skipped`, pieces found by several lanes merge into one
item, items are ordered by best lane rank and cut to `k`, and the status follows
the contract's precedence.

- **Deadlines:** a voice search answers within 5 s, embedding included, and
  lanes still running then are cancelled and reported `timed_out`. An MCP search
  has no deadline; each of its network calls still stops after 30 s. Lanes and
  the embedder are given the deadline so their calls stop inside it.
- **Trace:** `trace.stages_ms` holds each stage's own duration (`embed`,
  `lanes`, `merge`) and the `total`, plus each lane's blocking reads under the
  file they read (`corpus.db`, `sessions.db`, `resolve.json`, `wiki pages`), nested inside
  `lanes`, not additive; `code_rev` comes from the `CODE_REV` environment
  variable. A lane whose own call stops at the deadline is `timed_out`, like one
  the engine stops waiting for: `time_limit` raises that expiry as
  `DeadlineExceeded`. Any other timeout, including the 30 s cap in a search with
  no deadline, is `failed`.

- **Corpus lane** (`src/searcher/corpus.py`): Chroma's `contents` collection,
  each hit resolved in newsletter-assistant's `corpus.db`. A hit missing there is
  dropped; a blank title becomes the url; the date is when the item was saved.
  `corpus.db` is opened read-only through SQLite on a read-write mount of
  newsletter-assistant's data directory (`NEWSLETTER_DATA_DIR`; compose mounts
  `BACKUP_SRC_DIR`, which it requires), so SQLite can recreate the `-shm` file of a WAL database its
  writer has closed. The container runs as UID 1000, the writers' UID.

- **Sessions lane** (`src/searcher/sessions.py`): Chroma's `conversations`
  collection, keyed to a session by `source_ref` (`sessions:<id>`), each session
  resolved in newsletter-assistant's `sessions.db`, opened the same way as
  `corpus.db`. One item per session, at its best-ranked chunk. A session no
  longer stored, or whose timestamps do not parse, is dropped. The date is when
  the session started, as a UTC date; `summary` is the stored summary, null
  until one is written; the title is the indexer's (the summary's first line,
  or `Session <first 8 characters of the id>`), and that same form when a
  chunk carries none.

- **Wiki lane** (`src/searcher/wiki.py`): Chroma's `wiki` collection, each hit
  resolved through kp's `_index/resolve.json` and the rendered page, read from a
  read-only mount of kp's `data/wiki` (`WIKI_DIR`). A pruned entity, or a page
  missing, unparseable or naming another entity, is dropped; a page re-rendered
  since indexing is `stale`; `related` holds up to 4 titles. An unreadable or
  unknown-version `resolve.json` fails the lane. No date, so `since` does not
  filter it.

The notes and briefs lanes are not wired yet, so they are `skipped` and a request
for them gets `partial`. No caller uses the service yet.

## Run

```bash
uv run poe test-searcher                      # its suite (a separate pytest run)
uv run uvicorn searcher.app:app --port 8001   # locally
```

In compose it is the `searcher` service, reachable on the shared
`kos_network` as `kp-searcher:8000`.
