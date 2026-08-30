# fetch_extract_queue

Sensor-driven pipeline that turns Notion-captured URLs into extracted Topic
Cards stored locally for newsletter-assistant to retrieve on engagement.

Picks up rows after triage_knowledge_queue has classified them: sensor filter is
Status=Fetching AND Content Type ∈ SUPPORTED_CONTENT_TYPES ({youtube, arxiv,
medium, facebook, github, file_pdf, file_audio, article, other}). The fetcher
service's handler registry routes the URL by host; the article handler is a
catch-all for anything not yt/arxiv/medium/facebook/github/file_pdf/file_audio,
so article and other reach a real fetcher path. Triage also registers the
dynamic partition; this pipeline only runs the job. Triage is therefore the
sole writer to `queue_items` partition state — a Notion row reaching
Status=Fetching without going through triage (manual edit, env misroute,
restored queue.db) fails fast in `fetch_content` with a clickable Notion URL.

## DAG (per partition)

```
poll_notion_for_extract (sensor, every 15min)
        │  per Status=Fetching + Content Type ∈ SUPPORTED_CONTENT_TYPES row,
        │  MAX_TO_EXTRACT_PER_TICK cap; triage_knowledge_queue registers partition
        ▼
fetch_extract_queue_job  (partition_key = notion_page_id)
        │
        ▼
fetch_content ──► extract_metadata ──► extract_reading_card ──► publish_item
   │                    │         │              │
   │                    │         │              └──► Notion: Status=Ready + Name (extracted_title)
   │                    │         │                   + Description (core_mechanism)
   │                    │         │
   │                    │         └─ on failure (LLM error / required-fields check):
   │                    │            run_failure_sensor → Notion: Status=Failed + Error
   │                    │
   │                    ├──► extract_claims  (parallel with extract_reading_card; [reported]/[opinion] claims
   │                    │        │            → extraction_calls extract_claims row — attributed-lane wiki substrate)
   │                    │        ▼
   │                    │    extract_entities  (article-grounded candidates; shared prompt-cache prefix so the
   │                    │                       article body is served from cache on this second extract-time call
   │                    │                       → extraction_calls extract_entities row. The synthesize_wiki DAG
   │                    │                       reads these two docs later — this pipeline no longer writes wiki.db.)
   │                    │
   │                    └─ best-effort: one OpenAI call over the fetched body, writing
   │                       contributors_json / publisher on queue_items plus a
   │                       call_kind='metadata' extraction_calls row. Any failure is swallowed and
   │                       the asset still materialises — it does not block either branch below it.
   │
   └─ on failure (fetcher service returns problem+json or unreachable):
      run_failure_sensor → Notion: Status=Failed + Error

`fetch_content` calls the standalone `fetcher` service over dagster_network —
POST `/v1/fetch` for normal URLs, or POST `/v1/structure` when the
queue_items row has `raw_content_override` set (user ticked
`Use page body` in Notion; see `FetcherResource.structure`
in `resources.py` and the override branch in `assets.fetch_content`). For
`/v1/fetch`, the service is authoritative for source matching
(arxiv / youtube / medium / facebook / github / file_pdf / file_audio / article)
and quality-floor enforcement. `extract_metadata` runs one OpenAI call over the
fetched body before either branch below it. `extract_reading_card`
runs ExtractorRegistry (ThreeCallOpenAIExtractor) in-process. fetch_content +
extract_reading_card include `content_preview` / `narrative_preview` / `topic_card_preview`
metadata (head + tail of the content) for at-a-glance verification.
```

Local store: `data/queue.db` (SQLite) for fetch + extraction state (raw_content,
extracted Topic Card, provenance, contributors/publisher metadata,
per-source `extract_claims` + `extract_entities` docs). This pipeline no longer
writes `data/wiki.db` — the wiki-write lane
(attribute + render) moved to the `synthesize_wiki` DAG, which reads those two
extraction docs on a daily sweep. Lifecycle status (Queued / Fetching / Ready /
Failed) lives in Notion.

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
  a `URL` url property, a `Content Type` select property (youtube, arxiv, …),
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
  `publish_item` asset's Name (`topic_card.extracted_title`) and
  Description (`topic_card.core_mechanism`) — the full narrative, the
  Topic Card JSON, and raw_content stay in kp's local store.
