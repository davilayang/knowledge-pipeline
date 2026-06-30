# fetch_extract_queue

Sensor-driven pipeline that turns Notion-captured URLs into extracted Topic
Cards stored locally for newsletter-assistant to retrieve on engagement.

Picks up rows after triage_knowledge_queue has classified them: sensor filter is
Status=Fetching AND Content Type ∈ SUPPORTED_CONTENT_TYPES ({YouTube, arXiv,
Article, Other, Podcast}). The fetcher service's handler registry routes the URL by
host; the article handler is a catch-all for anything not yt/arxiv/pdf/medium/podcast,
so Article and Other reach a real fetcher path. Triage also registers the
dynamic partition; this pipeline only runs the job. Triage is therefore the
sole writer to `queue_items` partition state — a Notion row reaching
Status=Fetching without going through triage (manual edit, env misroute,
restored queue.db) fails fast in `fetched` with a clickable Notion URL.

## DAG (per partition)

```
poll_notion_for_extract (sensor, every 15min)
        │  per Status=Fetching + Content Type ∈ SUPPORTED_CONTENT_TYPES row,
        │  MAX_TO_EXTRACT_PER_TICK cap; triage_knowledge_queue registers partition
        ▼
fetch_extract_queue_job  (partition_key = notion_page_id)
        │
        ▼
fetched ──► extracted ──► published
   │           │              │
   │           │              └──► Notion: Status=Ready + Name (extracted_title)
   │           │                   + Description (core_mechanism)
   │           │
   │           └─ on failure (LLM error / required-fields check):
   │              run_failure_sensor → Notion: Status=Failed + Error
   │
   ├──► source_summary  (parallel with extracted; [fact]/[speculation] claims
   │                     → extraction_calls source_summary row — attributed-lane wiki substrate)
   │
   └─ on failure (fetcher service returns problem+json or unreachable):
      run_failure_sensor → Notion: Status=Failed + Error

`fetched` calls the standalone `fetcher` service over dagster_network —
POST `/v1/fetch` for normal URLs, or POST `/v1/structure` when the
queue_items row has `raw_content_override` set (user ticked
`Use page body` in Notion; see `FetcherResource.structure`
in `resources.py` and the override branch in `assets.fetched`). For
`/v1/fetch`, the service is authoritative for source matching
(article / arxiv / youtube) and quality-floor enforcement. `extracted`
runs ExtractorRegistry (ThreeCallOpenAIExtractor) in-process. fetched +
extracted include `content_preview` / `narrative_preview` / `topic_card_preview`
metadata (head + tail of the content) for at-a-glance verification.
```

Local store: `data/queue.db` (SQLite). Lifecycle status (Queued / Fetching /
Ready / Failed) lives in Notion; everything else (raw_content, extracted
Topic Card, provenance) lives in the local store.

URL→markdown is delegated to `services/fetcher/`. The asset enforces a
500-char extraction floor as the last line of defence before extraction
— see `assets.py`. Env wiring in `.env.example` under the `FETCHER_*`
block; service-side knobs (LlamaParse / SOCKS5 / Jina) live in the
fetcher service's own env.

## Runbook

```bash
# Manually trigger a partition (e.g. after a fetcher fix).
dg launch --job fetch_extract_queue --partition <notion_page_id>

# Backfill all currently Fetching rows.
dg launch --job fetch_extract_queue \
          --partition-range <first_id>...<last_id>

# Re-extract a page with a bumped prompt label (overwrites prior extraction).
# Bump the relevant PROMPT_LABEL_* constant in def_config.py AND add the new
# prompts/extraction/<label>.md file in the same commit, then re-launch:
dg launch --job fetch_extract_queue --partition <notion_page_id>

# Recovery — stuck in Fetching:
# Check the local store; if extraction row exists, manually flip Notion Status=Ready.
# If not, wait for any in-flight run to reach a terminal state first
# (the sensor's _has_in_flight_run guard skips page_ids with QUEUED /
# NOT_STARTED / STARTING / STARTED runs, so a Notion edit mid-flight has
# no effect). Once the run is terminal, edit the Notion row (any field) to
# bump last_edited_time so the sensor computes a fresh run_key on the next tick.
```

## External setup

- **Notion integration token** — Internal Integration in the personal
  Notion workspace; share the Queue DB with it.
- **`NOTION_QUEUE_DB_ID`** — the database id (with or without dashes).
- **Notion DB schema** — see `.env.example` for the required envs; the DB
  must have a native `Status` property (Notion's status property type,
  not a select) with options
  `Queued / Fetching / Ready / Engaging / Discussed / Archived / Failed`
  (triage additionally requires `Skipped`),
  a `URL` url property, a `Content Type` select property (YouTube, arXiv, …),
  and an `Error` rich-text property.
- **Fetcher service** — `FETCHER_URL` must point to a reachable
  `services/fetcher/` instance. In docker-compose the sidecar container
  resolves at `http://fetcher:8000` over `dagster_network`; for laptop
  `poe dagster-dev`, run `uv run uvicorn fetcher.app:app --workers 1
  --port 8000` from `services/fetcher/` and set
  `FETCHER_URL=http://localhost:8000`.
- **Notion AI training opt-out** — `Notion Workspace Settings → Notion AI
  → Manage data → "Don't use my workspace data to train models"` must be
  ON. The only LLM-derived content written back to Notion is the
  `published` asset's Name (`topic_card.extracted_title`) and
  Description (`topic_card.core_mechanism`) — the full narrative, the
  Topic Card JSON, and raw_content stay in kp's local store.
