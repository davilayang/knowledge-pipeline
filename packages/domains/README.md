# `knowledge-domains`

Pure data adapters. Files or DBs → typed `IngestItem`s.

## Rules

- **No LLM calls.** Anything that calls an LLM goes in `workflows/`.
- **No Dagster imports.** Anything that schedules goes in `orchestrators/`.
- **No ML deps.** Embedding/chunking/RAG infra lives in `retrievers/`.
- **No internal imports.** `domains` is the foundation — `workflows`, `retrievers`, `evals`, and `orchestrators` may depend on it; it does not depend on them.
- **Read-only by default.** Sources expose enumeration + by-id lookup; mutation is the writer's job (newsletter-assistant for sessions/research, scrapers for raw_store).

## Layout

```
src/domains/
├── types.py            # IngestItem + IngestSource Protocol — the shape every source yields
├── raw_store/          # raw_store.db — newsletter-assistant ingest store
│   └── sources.py      # RawStoreSource + ContentRow + query helpers
├── notes/              # Local markdown inbox
│   └── sources.py      # LocalFileSource
├── wiki/               # Wiki state — Postgres-backed run state
│   ├── state.py        # wiki.processed / wiki.pages run state
│   ├── aliases.py      # alias resolution
│   ├── io.py           # markdown page IO
│   └── schema/wiki.sql # Postgres schema
├── sessions/           # Voice-session SQLite (newsletter-assistant)
│   └── sources.py      # SessionsSource — also defines TURN_MARKER_PREFIX,
│                       # the marker format consumed by the turn_grouping
│                       # chunker in retrievers
├── research/           # Research-panel SQLite (newsletter-assistant)
│   └── sources.py      # ResearchSource
├── queue_store/        # queue.db — Notion Queue pipeline SQLite store
│   └── sources.py      # queue_items + extraction_calls tables; upsert/read helpers
│                       # consumed by triage + fetch_extract_queue pipelines
└── fetches_store/      # fetches.db — fetcher service SQLite store
    └── sources.py      # cache + fetches + url_aliases tables; upsert/read helpers
                        # consumed by the fetcher service
```

## `IngestItem`

The normalized shape every source yields. Pipelines (`synthesize_wiki`,
`populate_vector_store`) consume `list[IngestItem]` and don't care which
source produced them.

```python
@dataclass
class IngestItem:
    item_id: str
    title: str
    date: date | None
    text: str
    source_type: str          # "raw_store" | "local_file" | "sessions" | "research"
    source_ref: str           # e.g. "raw_store:abc123" or "sessions:s_done"
    author: str | None = None
    url: str | None = None
    started_at: datetime | None = None
```

The optional fields carry source-specific metadata that some adapters expose
and others don't — consumers read what they need.

## Source contract

The `IngestSource` Protocol in `types.py` requires only one method:

```python
class IngestSource(Protocol):
    def get_items(self) -> list[IngestItem]: ...
```

All concrete source classes additionally implement `get_item_ids` and `get_item`:

```python
def get_item_ids(self) -> list[str]: ...
def get_item(self, item_id: str) -> IngestItem | None: ...
```

`get_item_ids` is the cheap discovery path used by the `populate_vector_store`
`pending` asset to compute the indexable set; `get_item` is the per-item path
used by the ingest assets. Sources that filter by completion state (e.g. only
ended sessions) apply that filter inside `get_item_ids` — never index a row
that the writer hasn't committed.

| Source | DB / path | Completion gate | Notes |
|---|---|---|---|
| `RawStoreSource` | `raw_store.db` | none — immutable append | takes a `db_path`; pin or live |
| `LocalFileSource` | a directory of `*.md` | none — caller filters mtime | YAML frontmatter respected |
| `SessionsSource` | `sessions.db` | `WHERE ended_at IS NOT NULL` | concatenates `turns` into a marker-delimited body |
| `ResearchSource` | `research.db` | row in `documents` | reads `documents.content` directly (committed atomically with the row) |

## SQLite reads

All upstream SQLite stores (`raw_store.db`, `sessions.db`, `research.db`) run
in WAL mode — concurrent reads don't block writers. `SessionsSource` and
`ResearchSource` assert `PRAGMA journal_mode == 'wal'` on connect and raise
loudly if a future deploy flips it; otherwise we'd silently degrade to
blocking reads.

## Sessions: how the transcript flows

`SessionsSource` serializes a session's `turns` rows into a single text body
on the `IngestItem`, with each turn delimited by a marker line:

```
<<<TURN role=user ts=2026-04-01T14:32:01+00:00>>>
What is RAG?
<<<TURN role=assistant ts=2026-04-01T14:32:05+00:00>>>
RAG stands for retrieval-augmented generation...
```

The marker prefix is `domains.sessions.sources.TURN_MARKER_PREFIX`. The
`turn_grouping` chunker (registered in `retrievers.chunking.registry`)
imports this constant and parses on it to recover turn boundaries — that's
the only cross-package coupling for the sessions pipeline. All other
chunkers in the registry treat the body as opaque text and split on it
the same way they would any other markdown.

## Adding a new source

1. Pick a sub-package (`wiki/`, `sessions/`, `research/`, or a new one).
2. Lock the upstream schema in a top-of-file docstring — pin the columns
   and indices the source reads, plus where the writer lives.
3. Implement the three `IngestSource` methods.
4. If the upstream is SQLite, assert WAL on connect.
5. Apply any completion gate inside `get_item_ids` so callers can't index
   in-progress writes.
6. Add a fixture-driven test under `tests/domains/<sub-package>/`.
