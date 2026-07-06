# `populate_vector_store` runbook

30-min-cadence ingest of pending items from each domain source
(`raw_store`, `notes`, `sessions`, `wiki`) into ChromaDB collections. The
pipeline writes pre-computed OpenAI embeddings
(`text-embedding-3-small @ 1536` by default) and is the producer side of the
RAG split — query-side cutover happens in Phase F.

Lands **paused** (`default_status=STOPPED`). Manually launch for end-to-end
smoke until Phase G turns the schedule on.

## DAG (per scheduled tick)

```
schedule run_populate_vector_store   (cron */30 * * * *, STOPPED)
  │  fires the current half-hour's partition (YYYY-MM-DD-HH:MM)
  ▼
vector_store/pending   (30-min partition)
  │  for each of {raw_store, notes, sessions, wiki}:
  │    - source.get_item_ids()
  │    - collection.get(where={"content_id": {"$in": ...}}) in 500-id batches
  │      to find already-indexed content_ids
  │    - take the first MAX_PER_TICK_DEFAULT (=50) unindexed ids
  │  wiki only: an indexed entity is "done" only when its indexed page_hash
  │    matches the live one (resolve.json) — a rewritten page (same entity_id,
  │    new page_hash) re-lists so the stale vector is re-embedded (FM1b)
  │  outputs dict[source_name, list[item_id]]
  ▼
vector_store/contents             vector_store/conversations
vector_store/notes                vector_store/wiki
  │  sequential per-item loop:
  │    - source.get_item(id) → IngestItem
  │    - chunker(item.text) per CHUNKER_BY_SOURCE
  │    - embed via OpenAIEmbedder (one OpenAI call per item); for markdown
  │      chunkers the chunk's heading breadcrumb is prepended to the embedded
  │      text (the stored `document` field stays clean)
  │    - collection.delete(where={"content_id": id}) then upsert in 4000-id batches
  │  deterministic chunk ids: f"{item_id}::chunk-{i}"  → idempotent re-runs
```

- **Empty pending slice** for a source → that ingest asset short-circuits
  with `_no pending_`. Run is green; no Chroma writes.
- **Per-item failure** (chunking / embedding / Chroma error) is collected;
  successful items in the same tick stay committed (upsert is idempotent).
  After the fan-out, if any item raised, the asset raises `dg.Failure` so
  Dagster retry replays the slice — successful items become no-ops on retry
  via the `delete + upsert` cycle and deterministic chunk ids.
- **Producer writes pre-computed embeddings.** The collections are accessed
  via `VectorStoreResource`; the resource's embedding-fn path is unused on
  write. Query-side OpenAI EF wiring is deferred to Phase F.
- **Heading-aware embeddings for markdown chunkers.** For sources whose
  chunker is `markdown` (raw_store, notes), the chunk's heading
  breadcrumb is prepended to the text before embedding (e.g.
  `"Introduction > Setup\n\n<chunk body>"`) to improve retrieval ranking
  within document sections. The stored Chroma `document` field stays
  unchanged — only the embedded vector encodes the breadcrumb.

## Chunk metadata schema

Every upserted chunk carries this metadata:

| Field | Always present | Description |
|---|---|---|
| `content_id` | yes | Upstream item id. Used by `delete(where=...)` for idempotent re-ingest. |
| `chunk_index` | yes | Position of this chunk in the item's chunk sequence. |
| `_embedding_model` | yes | Model id baked into the vector (e.g. `text-embedding-3-small`). |
| `_embedding_dims` | yes | Vector dimension (e.g. `1536`). |
| `heading_path` | optional | Hierarchical heading breadcrumb joined by ` > ` for markdown-chunked items (e.g. `"Doc Title > Section One"`); time-range string for `turn_grouping` chunks (sessions); absent for items without markdown structure. |
| `title` | optional | Upstream item title. |
| `author` | optional | Upstream item author. |
| `content_date` | optional | ISO-formatted `IngestItem.date`. |
| `url` | optional | Upstream URL. |
| `started_at` | optional | ISO-formatted `IngestItem.started_at` (sessions). |
| `source_ref` | optional | Source-specific reference (e.g. `raw_store:<id>`). |
| `num_sources` | optional | wiki: distinct content items behind the entity — lets the recall side hedge a single-source page (FM4). |
| `page_hash` | optional | wiki: per-entity content hash from `resolve.json` — the freshness key + the recall side's stale-hit check (FM2). |
| `snapshot_id` | optional | wiki: tick-wide `resolve.json` fingerprint stamped on every entity that tick (FM2). |

## Environment variables

| Var | Required | Purpose |
|---|---|---|
| `BACKUP_SRC_DIR` | yes | Root dir holding `raw_store.db`, `sessions.db`, and `notes/` (the synced newsletter-assistant data). Bound to `SourcesResource.backup_source_dir`. The `wiki` source does **not** read here — it is kp-owned and roots at `LOCAL_WIKI_DIR` (`DATA_DIR/wiki`, `config.py`). |
| `OPENAI_API_KEY` | yes | OpenAI embeddings calls. |
| `CHROMA_HOST` | yes | Chroma HTTP host — `chroma` in compose; `localhost` for local `poe dagster-dev` against an external `chroma run`. |
| `CHROMA_PORT` | yes | Chroma HTTP port (8000 default). |

Embedding model + dims (`text-embedding-3-small`, 1536) and the per-source
cap (50) are code constants in `def_config.py` — they're coupled to the
existing vectors and don't vary per deploy.

## Operations

### Manual launch (deployed — Phase E onwards)

Chroma runs as a sibling service in docker compose; dagster-code reaches it
at `chroma:8000` via the compose network. The schedule is paused; trigger a
single partition manually from the Dagster UI or via the CLI:

```bash
docker compose exec dagster-code \
  dg launch -m orchestrators.definitions --job populate_vector_store \
    --partition "$(date -u +%Y-%m-%d-%H:00)"
```

### Manual launch (local `poe dagster-dev`)

Start the compose `chroma` service (loopback-only port 8000), then run the
local Dagster against it:

```bash
docker compose --profile data up -d   # starts postgres + chroma
set -a; source .env; set +a

uv run dg launch -m orchestrators.definitions --job populate_vector_store \
  --partition "$(date -u +%Y-%m-%d-%H:00)"
```

Data persists in the `chroma_data` named volume — wipe with
`docker compose down -v` to reset.

Verify the collections:

```bash
uv run python -c "
import os, chromadb
c = chromadb.HttpClient(
    host=os.environ.get('CHROMA_HOST', 'localhost'),
    port=int(os.environ.get('CHROMA_PORT', 8000)),
)
for name in ('contents','conversations','notes','wiki'):
    print(name, c.get_or_create_collection(name, embedding_function=None).count())
"
```

### Pending stays full for >2 ticks

The per-source `MAX_PER_TICK_DEFAULT=50` cap with a 30-min schedule drains
9600 items/day across the four sources. If `pending_by_source` keeps growing:

- Check `OPENAI_API_KEY` quota / 429 patterns in run logs.
- Confirm `BACKUP_SRC_DIR` is the up-to-date backup landing (raw_store, notes, and sessions
  read from the live mount, not a partition snapshot; wiki roots at `LOCAL_WIKI_DIR`, not here).
- One-shot backfill: bump `MAX_PER_TICK_DEFAULT` in `def_config.py` and
  launch a single partition manually.

### Re-embed a single item

```bash
# Manually delete its chunks; the next tick will re-pick it up via the
# pending discovery.
uv run python -c "
import os, chromadb
c = chromadb.HttpClient(
    host=os.environ.get('CHROMA_HOST', 'localhost'),
    port=int(os.environ.get('CHROMA_PORT', 8000)),
)
col = c.get_or_create_collection('contents', embedding_function=None)
col.delete(where={'content_id': '<item_id>'})
"
```

### Switching embedding model / dims

Embedding model and dims are baked into every chunk's metadata
(`_embedding_model`, `_embedding_dims`). Changing the env vars without
purging the collection will mix vectors of different shape → distance
math becomes garbage. Per the rebuild-don't-migrate doctrine, drop the
relevant collection first:

```bash
uv run python -c "
import os, chromadb
c = chromadb.HttpClient(
    host=os.environ.get('CHROMA_HOST', 'localhost'),
    port=int(os.environ.get('CHROMA_PORT', 8000)),
)
c.delete_collection('contents')
"
```

## Out of scope (deferred)

- Phase F: consumer-side cutover (HttpClient, query-side OpenAI EF on
  `VectorStoreResource`, 4-fan-out recall).
- Phase G: drop POC volume, raised `MAX_PER_TICK` backfill, enable schedule.
- Orphan GC for chunks whose upstream items were deleted — including **pruned
  wiki entities** (merged/renamed/removed): their old `entity_id` vectors linger
  in the `wiki` collection because discovery only iterates live pages. Accepted
  as FM1 noise (NA skips dead `entity_id`s); revisit if merge/rename drift
  measurably pollutes recall.
- Sensor-based discovery to replace the cron schedule.

## Future optimizations

These aren't needed at the current `MAX_PER_TICK_DEFAULT=50` and 30-min
cadence (each tick is well under a minute). If per-tick latency ever becomes
a constraint, the highest-leverage change is **cross-item embedding batching**:
today each ingest asset makes one OpenAI request per item (so up to 50
per-source-per-tick); collapsing those into a single
`OpenAIEmbedder.embed_batch(all_texts_across_items)` call (which sub-batches at
250k tokens internally) would cut request count 10-50x without changing the
per-item idempotent contract — the embedding phase becomes a single fan-out,
then a sequential delete-then-upsert loop writes one item at a time using its
pre-computed slice of the result.

This matches the pattern dagster-open-platform uses across all their
external-API assets (fetch → serial loop → batch-write at the end). The
deferred reason is simple: throughput hasn't been a constraint yet, and
adding it early would buy nothing observable.

Anti-patterns to skip:
- `ThreadPoolExecutor` inside the asset — non-idiomatic for both this repo
  (see `fetch_extract_queue/assets.py`) and DOP. Plain Python, not Dagster-native.
- `DynamicOutput` / graph-backed asset for fan-out — ~100 LOC plus 1-2s/op
  Dagster orchestration overhead per item; only worth it when per-item work
  is genuinely heavy (minutes, not seconds).
