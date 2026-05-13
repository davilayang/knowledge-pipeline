# `populate_vector_store` runbook

30-min-cadence ingest of pending items from each of the four domain sources
into ChromaDB collections. The pipeline writes pre-computed OpenAI embeddings
(`text-embedding-3-small @ 1536` by default) and is the producer side of the
RAG split — query-side cutover happens in Phase F.

Lands **paused** (`default_status=STOPPED`). Manually launch for end-to-end
smoke until Phase G turns the schedule on.

## DAG (per scheduled tick)

```
schedule run_populate_vector_store   (cron */30 * * * *, STOPPED)
  │  fires the current hour's partition (YYYY-MM-DD-HH:MM)
  ▼
vector_store/pending   (hourly partition)
  │  for each of {raw_store, notes, sessions, research}:
  │    - source.get_item_ids()
  │    - collection.get(where={"content_id": {"$in": ...}}) in 500-id batches
  │      to find already-indexed content_ids
  │    - take the first MAX_PER_TICK_DEFAULT (=50) unindexed ids
  │  outputs dict[source_name, list[item_id]]
  ▼
vector_store/contents             vector_store/conversations
vector_store/notes                vector_store/research_documents
  │  parallel fan-out (ThreadPoolExecutor, INGEST_CONCURRENCY=4):
  │    - source.get_item(id) → IngestItem
  │    - chunker(item.text) per CHUNKER_BY_SOURCE
  │    - embed via OpenAI (250k-token sub-batches, tenacity retry on transient)
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

## Environment variables

| Var | Required | Purpose |
|---|---|---|
| `BACKUP_SOURCE_DIR` | yes | Root dir holding `raw_store.db`, `sessions.db`, `research.db`, and `notes/`. Bound to `SourcesResource.backup_source_dir`. |
| `OPENAI_API_KEY` | yes | OpenAI embeddings calls. |
| `CHROMA_HOST` | yes | Chroma HTTP host (e.g. `localhost` for local smoke, `chroma` in compose). |
| `CHROMA_PORT` | yes | Chroma HTTP port (8000 default). |
| `OPENAI_EMBEDDING_MODEL` | no | Default `text-embedding-3-small`. |
| `OPENAI_EMBEDDING_DIMS` | no | Default `1536`. Matches the eval-winning baseline. |
| `VECTOR_STORE_MAX_PER_TICK` | no | Per-source cap (default 50). Raise for backfill. |
| `VECTOR_STORE_INGEST_CONCURRENCY` | no | Threads per ingest asset (default 4). |

## Operations

### Manual launch (Phase D — schedule paused)

Local Chroma:

```bash
chroma run --path /tmp/chroma_pvs --port 8000 &
set -a; source .env; set +a

uv run dg launch \
  --job populate_vector_store \
  --partition "$(date -u +%Y-%m-%d-%H:00)"
```

Verify the four collections:

```bash
uv run python -c "
import chromadb
c = chromadb.HttpClient(host='localhost', port=8000)
for name in ('contents','conversations','notes','research_documents'):
    print(name, c.get_or_create_collection(name, embedding_function=None).count())
"
```

### Pending stays full for >2 ticks

The per-source `MAX_PER_TICK_DEFAULT=50` cap with a 30-min schedule drains
9600 items/day across the four sources. If `pending_by_source` keeps growing:

- Check `OPENAI_API_KEY` quota / 429 patterns in run logs.
- Confirm `BACKUP_SOURCE_DIR` is the up-to-date backup landing (the four
  sources read from the live mount, not a partition snapshot).
- One-shot backfill: bump `VECTOR_STORE_MAX_PER_TICK` to a large number
  and launch a single partition manually.

### Re-embed a single item

```bash
# Manually delete its chunks; the next tick will re-pick it up via the
# pending discovery.
uv run python -c "
import chromadb
c = chromadb.HttpClient(host='localhost', port=8000)
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
import chromadb
c = chromadb.HttpClient(host='localhost', port=8000)
c.delete_collection('contents')
"
```

## Out of scope (deferred)

- Phase E: `chroma` service in shared docker compose.
- Phase F: consumer-side cutover (HttpClient, query-side OpenAI EF on
  `VectorStoreResource`, 4-fan-out recall).
- Phase G: drop POC volume, raised `MAX_PER_TICK` backfill, enable schedule.
- Orphan GC for chunks whose upstream items were deleted.
- Sensor-based discovery to replace the cron schedule.
