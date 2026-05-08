# `knowledge-domains`

Pure data adapters. Files or DBs → typed `IngestItem`s.

## Rules

- **No LLM calls.** Anything that calls an LLM goes in `workflows/`.
- **No Dagster imports.** Anything that schedules goes in `orchestrators/`.
- **No ML deps.** Embedding/chunking/RAG infra lives in `retrievers/`.
- **Read-only by default.** Sources expose enumeration + by-id lookup; mutation is the writer's job (newsletter-assistant for sessions/research, scrapers for raw_store).

## Layout

```
src/domains/
├── types.py            # Shared data types (IngestItem)
├── store.py            # raw_store.db row types and query helpers
├── wiki/               # Wiki state — Postgres-backed run state
│   ├── sources.py      # RawStoreSource, LocalFileSource (re-exports IngestItem)
│   ├── state.py        # wiki.processed / wiki.pages run state
│   ├── aliases.py      # alias resolution
│   ├── io.py           # markdown page IO
│   └── schema/wiki.sql # Postgres schema
├── sessions/           # Voice-session SQLite (newsletter-assistant)
│   ├── sources.py      # SessionsSource
│   └── chunking.py     # turn_grouping_chunker
└── research/           # Research-panel SQLite (newsletter-assistant)
    └── sources.py      # ResearchSource
```

## Source contract

Every source exposes:

```python
class IngestSource(Protocol):
    def get_item_ids(self) -> list[str]: ...
    def get_item(self, item_id: str) -> IngestItem | None: ...
    def get_items(self) -> list[IngestItem]: ...
```

`get_item_ids` is the cheap discovery path used by the `populate_vector_store`
`pending` asset to compute the indexable set; `get_item` is the per-item path
used by the ingest assets. Sources that filter by completion state (e.g. only
ended sessions) apply that filter inside `get_item_ids`.

## SQLite reads

All upstream SQLite stores (`raw_store.db`, `sessions.db`, `research.db`) run in
WAL mode — concurrent reads don't block writers. Source classes assert
`PRAGMA journal_mode == 'wal'` on connect and raise loudly if a future deploy
flips it; otherwise we'd silently degrade to blocking reads.
