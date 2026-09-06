# `knowledge-domains`

Pure data adapters. Files or DBs → typed `IngestItem`s.

## Rules

- **No LLM calls.** Anything that calls an LLM goes in `workflows/`.
- **No Dagster imports.** Anything that schedules goes in `orchestrators/`.
- **No ML deps.** Embedding/chunking/RAG infra lives in `retrievers/`.
- **No internal imports.** `domains` is the foundation — `workflows`, `retrievers`, `evals`, and `orchestrators` may depend on it; it does not depend on them.
- **Read-only by default.** Sources expose enumeration + by-id lookup; mutation is the writer's job (newsletter-assistant for sessions, scrapers for raw_store).

## Layout

```
src/domains/
├── types.py            # IngestItem + IngestSource Protocol — the shape every source yields
├── extraction/         # The three-call extraction payload — models, rendering, call rows
│   ├── schemas.py      # TopicCard / Followups / Narrative pydantic models. Narrative's field
│   │                   # titles are the section headers newsletter-assistant reads aloud, so
│   │                   # which fields exist is governed by the cross-repo narrative contract
│   ├── render.py       # render_narrative — Narrative → the headed text the voice agent speaks
│   ├── records.py      # ExtractionCallRecord — one row per LLM call in queue.db extraction_calls
│   └── prompts.py      # strip_design_notes — drops a prompt file's header so only the body ships
├── raw_store/          # corpus.db — newsletter-assistant ingest store
│   └── sources.py      # RawStoreSource + ContentRow + query helpers
├── notes/              # Local markdown inbox
│   ├── sources.py      # LocalFileSource
│   └── promoted.py     # PromotedNote + read_promoted_notes — frontmatter-aware
│                       # reader for `promote: true` notes (KP-2 note→wiki promotion)
├── wiki/               # Wiki state — SQLite-backed (wiki.db) run state + page IO
│   ├── types.py        # WikiPage / ExtractedEntity / ExtractionResult + PageType literal
│   ├── state.py        # entities / pages / aliases / processed_items / rejected_entities run state
│   ├── aliases.py      # alias resolution + AliasStore
│   ├── identity.py     # Candidate / EntityRecord / ResolvedEntity + resolve_or_mint_batch
│   ├── mentions.py     # surface-form mention counter (count_mentions) — word-boundary, case-insensitive; used by entity_assignment.match_claim
│   ├── io.py           # markdown page IO (read_page / read_meta / write_page)
│   ├── sources.py      # WikiSource — synthesized .md pages → IngestItems
│   ├── reject_cli.py   # wiki-reject — delete + tombstone noise entities (reject_entity)
│   ├── merge_cli.py    # wiki-merge — fold a duplicate entity into a survivor (state.merge_entities)
│   ├── dedup.py        # merge-candidate data (EntityText/CandidatePair) + wiki.db reader (load_entity_texts) + lexical name-only search (find_name_candidates); the numeric embedding search lives in evals.wiki_dedup
│   ├── claims.py # parse/render per-source [reported]/[opinion] claim files (Layer 1.5)
│   ├── units.py        # build_citable_units/number_units — split a source body into numbered citable units a claim can cite
│   ├── CURATION.md     # operator runbook: reject noise entities
│   └── schema/wiki.sql # SQLite schema
├── sessions/           # Voice-session SQLite (newsletter-assistant)
│   └── sources.py      # SessionsSource — also defines TURN_MARKER_PREFIX,
│                       # the marker format consumed by the turn_grouping
│                       # chunker in retrievers
├── queue_store/        # queue.db — Notion Queue pipeline SQLite store
│   └── sources.py      # queue_items + extraction_calls tables; upsert/read helpers
│                       # consumed by triage + fetch_extract_queue pipelines
└── fetch_store/      # fetch.db — fetcher service SQLite store
    └── sources.py      # cache + fetches + url_aliases tables; upsert/read helpers
                        # consumed by the fetcher service
```

## `IngestItem`

The normalized shape every source yields. Pipelines (`populate_vector_store`,
`fetch_extract_queue` attributed lane) consume `list[IngestItem]` and don't
care which source produced them.

```python
@dataclass
class IngestItem:
    item_id: str
    title: str
    date: date | None
    text: str
    source_type: str          # "raw_store" | "local_file" | "sessions" | "wiki"
    source_ref: str           # e.g. "raw_store:abc123" or "sessions:s_done"
    author: str | None = None
    url: str | None = None
    started_at: datetime | None = None
    num_sources: int | None = None   # wiki: distinct content items behind the entity
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
`pending` asset to compute the indexable set; `get_item`
is the per-item path used by the ingest assets. Sources that filter by
completion state (e.g. only ended sessions that recorded dialogue) apply that
filter inside `get_item_ids` — never index a row that the writer hasn't
committed. `RawStoreSource` and `LocalFileSource` both extend the base
signature with an optional `with_body: bool = False` parameter; passing `True`
additionally drops items with no body — `content_md` NULL/blank for
`RawStoreSource`, frontmatter-stripped text empty for `LocalFileSource` — so
`populate_vector_store`'s `pending` asset doesn't re-select forever an item
that chunks to nothing and so never shows up as "already indexed".

| Source | DB / path | Completion gate | Notes |
|---|---|---|---|
| `RawStoreSource` | `corpus.db` | none — immutable append | takes a `db_path`; pin or live |
| `LocalFileSource` | a directory of `*.md` | none by default; opt-in `with_body=True` drops files with no body after frontmatter is stripped | YAML frontmatter respected |
| `SessionsSource` | `sessions.db` | `WHERE ended_at IS NOT NULL` and a `user_msg`/`assistant_msg` event exists | concatenates `events` (user_msg/assistant_msg) into a marker-delimited body |
| `WikiSource` | a `data/wiki/` dir of `.md` pages | none — page on disk | one page → one item; `text` is the page **summary**, `num_sources` carried for the W3 sparsity gate; skips `_index/` sidecars |

## SQLite reads

All upstream SQLite stores (`corpus.db`, `sessions.db`) run
in WAL mode — concurrent reads don't block writers. `SessionsSource`
asserts `PRAGMA journal_mode == 'wal'` on connect and raises
loudly if a future deploy flips it; otherwise we'd silently degrade to
blocking reads.

## Sessions: how the transcript flows

`SessionsSource` serializes a session's `events` rows (user_msg/assistant_msg) into a single text body
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

1. Pick a sub-package (`wiki/`, `sessions/`, or a new one).
2. Lock the upstream schema in a top-of-file docstring — pin the columns
   and indices the source reads, plus where the writer lives.
3. Implement the three `IngestSource` methods.
4. If the upstream is SQLite, assert WAL on connect.
5. Apply any completion gate inside `get_item_ids` so callers can't index
   in-progress writes.
6. Add a fixture-driven test under `tests/domains/<sub-package>/`.
