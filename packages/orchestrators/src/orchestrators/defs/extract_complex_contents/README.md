# extract_complex_contents

Sensor-driven pipeline that turns Notion-captured URLs into extracted Topic
Cards stored locally for newsletter-assistant to retrieve on engagement.

Picks up rows after triage_queued_items has classified them: sensor filter is
Status=Fetching AND Content Type ∈ SUPPORTED_CONTENT_TYPES ({YouTube, arXiv}).
Triage also registers the dynamic partition; this pipeline only runs the job.

## DAG (per partition)

```
poll_notion_for_extract (sensor, every 15min)
        │  per Status=Fetching + Content Type ∈ SUPPORTED_CONTENT_TYPES row,
        │  MAX_TO_EXTRACT_PER_TICK cap; triage_queued_items registers partition
        ▼
extract_complex_contents_job  (partition_key = notion_page_id)
        │
        ▼
fetched ──► extracted ──► published
   │           │              │
   │           │              └──► Notion: Status=Ready
   │           │
   │           └─ on failure (LLM error / required-fields check):
   │              run_failure_sensor → Notion: Status=Failed + Error
   │
   └─ on failure (per-type fetcher cascade exhausted / under floor):
      run_failure_sensor → Notion: Status=Failed + Error

Each asset dispatches to per-type strategies (FetcherResource for fetched,
ExtractorRegistry for extracted) — no branching in the asset graph itself.
fetched + extracted include `content_preview` / `extraction_preview`
metadata (head + tail of the content) for at-a-glance verification.
```

Local store: `data/queue.db` (SQLite). Lifecycle status (Queued / Fetching /
Ready / Failed) lives in Notion; everything else (raw_content, extracted
Topic Card, provenance) lives in the local store.

## arXiv PDF rendering — LlamaParse (kp) vs pymupdf4llm (NA)

kp uses LlamaParse (LlamaCloud, `agentic_plus` tier) for arXiv PDF →
markdown. Hard-fails on any LlamaParse failure — no pymupdf4llm fallback.
Latency is acceptable here because kp is the async ingestion layer; the
agent (`newsletter-assistant`) doesn't wait on extraction.

NA's equivalent fetcher uses pymupdf4llm (faster, lower quality) because
the agent layer is user-facing.

Required env: `LLAMA_CLOUD_API_KEY` (LlamaCloud API key), `LLAMA_PARSE_TIER`
(tier string, e.g. `agentic_plus` for prod or `fast` for dev — see
`FetcherResource.llama_parse_tier`). `llama_cloud_base_url` defaults to
`https://api.cloud.eu.llamaindex.ai` on `FetcherResource`.

## Runbook

```bash
# Manually trigger a partition (e.g. after a fetcher fix).
dg launch --job extract_complex_contents --partition <notion_page_id>

# Backfill all currently Fetching rows.
dg launch --job extract_complex_contents \
          --partition-range <first_id>...<last_id>

# Re-extract a page with a bumped prompt label (overwrites prior extraction).
# Bump the relevant PROMPT_LABEL_* constant in def_config.py AND add the new
# prompts/extraction/<label>.md file in the same commit, then re-launch:
dg launch --job extract_complex_contents --partition <notion_page_id>

# Recovery — stuck in Fetching:
# Check the local store; if extraction row exists, manually flip Notion Status=Ready.
# If not, edit the Notion row (any field) to bump last_edited_time so the
# sensor's run_key generates a fresh run on the next tick.
```

## External setup

- **Notion integration token** — Internal Integration in the personal
  Notion workspace; share the Queue DB with it.
- **`NOTION_QUEUE_DB_ID`** — the database id (with or without dashes).
- **Notion DB schema** — see `.env.example` for the required envs; the DB
  must have a `Status` select property with options
  `Queued / Fetching / Ready / Engaging / Discussed / Archived / Failed`,
  a `URL` url property, a `Content Type` select property (YouTube, arXiv, …),
  and an `Error` rich-text property.
- **Pi SOCKS5 proxy** — `PI_SOCKS5_URL` reachable from the Dagster host;
  used as the curl-cffi fallback when Jina is blocked.
- **Notion AI training opt-out** — `Notion Workspace Settings → Notion AI
  → Manage data → "Don't use my workspace data to train models"` must be
  ON. Lifecycle-only Notion writes are intentional (raw content + Topic
  Cards stay in kp's local store).
