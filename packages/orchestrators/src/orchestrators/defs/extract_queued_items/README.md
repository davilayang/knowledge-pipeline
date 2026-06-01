# extract_queued_items

Sensor-driven pipeline that turns Notion-captured URLs into extracted Topic
Cards stored locally for newsletter-assistant to retrieve on engagement.

## DAG (per partition)

```
poll_notion_queue (sensor, every 15min)
        │  per Status=Queued row, MAX_QUEUED_PER_TICK cap
        ▼
extract_queued_items_job  (partition_key = notion_page_id)
        │
        ▼
fetched_content ──► topic_card ──► persisted
      │                 │              │
      │                 │              └──► Notion: Status=Ready
      │                 │
      │                 └─ on failure (LLM error / required-fields check):
      │                    run_failure_sensor → Notion: Status=Failed + Error
      │
      └─ on failure (Jina+curl_cffi cascade exhausted / under floor):
         run_failure_sensor → Notion: Status=Failed + Error
```

Local store: `data/queue.db` (SQLite). Lifecycle status (Queued / Fetching /
Ready / Failed) lives in Notion; everything else (raw_content, extracted
Topic Card, provenance) lives in the local store.

## Runbook

```bash
# Manually trigger a partition (e.g. after a fetcher fix).
dg launch --job extract_queued_items --partition <notion_page_id>

# Backfill all currently Queued rows.
dg launch --job extract_queued_items \
          --partition-range <first_id>...<last_id>

# Re-extract a page with a bumped prompt label (overwrites prior extraction).
EXTRACT_QUEUE_PROMPT_LABEL=v6_kp_copy_<date> \
  dg launch --job extract_queued_items --partition <notion_page_id>

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
  a `URL` url property, and an `Error` rich-text property.
- **Pi SOCKS5 proxy** — `PI_SOCKS5_URL` reachable from the Dagster host;
  used as the curl-cffi fallback when Jina is blocked.
- **Notion AI training opt-out** — `Notion Workspace Settings → Notion AI
  → Manage data → "Don't use my workspace data to train models"` must be
  ON. Lifecycle-only Notion writes are intentional (raw content + Topic
  Cards stay in kp's local store).
