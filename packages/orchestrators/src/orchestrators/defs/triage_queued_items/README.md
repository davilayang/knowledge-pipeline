# triage_queued_items

Sensor-driven pipeline that classifies Notion-captured URLs by host and routes
them: Tier A (YouTube, arXiv) → Status=Fetching (extract_complex_contents picks
up); Tier B (Article, Other) → Status=Ready (newsletter-assistant fetches at
engagement time).

## DAG (per partition)

```
poll_notion_for_triage (sensor, every 15min)
        │  per Status=Queued OR Status=empty row,
        │  MAX_QUEUED_PER_TICK=10 cap; registers notion_page_id dynamic partition
        ▼
triage_queued_items_job  (partition_key = notion_page_id)
        │
        └──► triaged  (resolve Content Type (Notion override > URL classifier),
                       canonicalize URL, commit to local store + Notion)
                │
                ├──► Tier A (YouTube / arXiv): Notion Status=Fetching
                │    extract_complex_contents sensor picks up next tick
                │
                └──► Tier B (Article / Other): Notion Status=Ready
                     NA fetches on user engagement
```

On any run failure `mark_notion_failed_on_triage` writes
Status=Failed + Error back to the Notion row.

## Resources

| Resource | Class | Purpose |
|---|---|---|
| `triage_notion` | `TriageNotionResource` | Reads Queue data source (Status=Queued / empty); writes Content Type + Status. Pipeline-scoped key to avoid collision with `extract_complex_contents`'s `notion`. |
| `triage_store` | `TriageQueueStore` | Writes to `data/queue.db` (kp local SQLite) via `domains.raw_store.queue`. Pipeline-scoped key to avoid collision with `extract_complex_contents`'s `store`. |

Triage never writes Notion's `Name` property — downstream `extract_complex_contents` (Tier A, from LLM extraction) or NA-at-engagement (Tier B) fills it from real content. Empty Name rows in Notion display URL-only until then.

## User overrides

The sensor reads three fields from each Notion row and passes them as typed config to the asset:

| Field | Behavior |
|---|---|
| `URL` | Required input. Asset fails fast if missing. |
| `Content Type` (SELECT) | **User override.** If set to a value in `ALL_CONTENT_TYPES` (`Article`/`YouTube`/`arXiv`/`PDF`/`Podcast`/`Other`), used as-is and written back unchanged. If empty or typo'd, falls back to URL classifier. The materialization metadata field `content_type_source` records which path was taken (`notion` vs `classified`). |
| `Name` (title) | Metadata-only passthrough. Asset surfaces it in the run materialization for observability but does not write it back to Notion or persist it to the local store. |

`Status` is system-controlled — never set by user before triage. The sensor's filter is `Status=Queued OR empty`; triage writes `Fetching` / `Ready` / `Failed` as the workflow signal.

## Env vars

No new env vars beyond what `extract_complex_contents` already requires:

| Var | Required | Description |
|---|---|---|
| `NOTION_INTEGRATION_TOKEN` | yes | Internal Integration secret |
| `NOTION_QUEUE_DB_ID` | yes | Knowledge OS Queue DB id |
| `NOTION_QUEUE_DATA_SOURCE_ID` | yes | Data source id under the Queue DB |

## Preconditions

The Notion Queue DB must have a `Content Type` SELECT property with options:

- `Article`
- `YouTube`
- `arXiv`
- `Other`

PDF and Podcast options are intentionally absent in v1 — those content types
fall through to Article (Tier B) until the fetchers land in
extract_complex_contents and the Notion options are added.

## Runbooks

**Row stuck in Status=Queued forever:**
Most likely a `Status` Notion option mismatch — the DB doesn't have a
`Ready` or `Fetching` option. Open the DB schema in Notion
(`...` → Edit database → Status property) and add the missing option.

**Row ends at Status=Failed:**
`mark_notion_failed_on_triage` fired. Check the Notion row's `Error`
rich-text property for the failure message, then check the Dagster run logs for
the full traceback (`dagster job logs --run-id <id>`).

**Re-triage a row:**
Edit any field on the Notion row (or flip Status back to Queued) to bump
`last_edited_time`. The sensor uses `run_key=triage-{page_id}-{last_edited}` so
a bumped timestamp generates a fresh RunRequest on the next tick.

## DAG version

`TRIAGE_QUEUED_ITEMS_DAG_VERSION = "1"` in `orchestrators/config.py`.
Bump when DAG logic changes (asset graph topology, classification logic, routing
policy). Independent of the package version.
