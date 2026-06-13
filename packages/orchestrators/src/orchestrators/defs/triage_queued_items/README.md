# triage_queued_items

Sensor-driven pipeline that classifies Notion-captured URLs by host,
canonicalises them, and writes Status=Fetching so
`extract_complex_contents` claims the row on the next tick. The fetcher
service's handler registry (article handler is the catch-all) determines
which tier serves the URL; the orchestrator side no longer routes by
content-type tier.

## DAG (per partition)

```
poll_notion_for_triage (sensor, every 15min)
        │  per Status=Queued OR Status=empty row,
        │  MAX_QUEUED_PER_TICK=10 cap; registers notion_page_id dynamic partition
        ▼
triage_queued_items_job  (partition_key = notion_page_id)
        │
        ├──► enriched  (pure-I/O: YouTube oEmbed / arXiv Atom API / article HTML
        │              meta → enrichment_json on queue_items. Failure-tolerant;
        │              empty signals on per-source HTTP error.)
        │
        └──► triaged  (consumes enriched; resolve Content Type (Notion override >
                       URL classifier), canonicalize URL; Podcast → YouTube
                       substitution via podcast_canonicalize.py on map hit;
                       classify content_shape via rules over enrichment +
                       URL; commit to local store + Notion. Notion Content
                       Shape property write lands in Phase 4.)
                │
                ├──► canonical_url matches an existing queue_items row?
                │    → Notion Status=Skipped, Error="Duplicate of <other_page_id>"
                │      (no queue.db write; original cohort stays the single source)
                │
                └──► Notion Status=Fetching
                     extract_complex_contents sensor picks up next tick;
                     fetcher service routes by URL (article = catch-all)
```

On any run failure `mark_notion_failed_on_triage` writes
Status=Failed + Error back to the Notion row.

## Resources

| Resource | Class | Purpose |
|---|---|---|
| `triage_notion` | `TriageNotionResource` | Reads Queue data source (Status=Queued / empty); writes Content Type + Status. Pipeline-scoped key to avoid collision with `extract_complex_contents`'s `notion`. |
| `triage_store` | `TriageQueueStore` | Writes to `data/queue.db` (kp local SQLite) via `domains.queue_store.sources`. Pipeline-scoped key to avoid collision with `extract_complex_contents`'s `store`. |

Triage seeds Notion's `Name` from the fetched page title (via `fetch_url_meta`) only when the user left Name blank — never overwrites a user-set title. Downstream `extract_complex_contents.published` later upgrades Name to `topic_card.extracted_title` once the LLM has produced a sharper read.

## User overrides

The sensor reads three fields from each Notion row and passes them as typed config to the asset:

| Field | Behavior |
|---|---|
| `URL` | Required input. Asset fails fast if missing. |
| `Content Type` (SELECT) | **User override.** If set to a value in `ALL_CONTENT_TYPES` (`Article`/`YouTube`/`arXiv`/`PDF`/`Podcast`/`Other`), used as-is and written back unchanged. If empty or typo'd, falls back to URL classifier. The materialization metadata field `content_type_source` records which path was taken (`notion` vs `classified`). |
| `Content Shape` (SELECT) | **User override.** If set to a value in `ALL_CONTENT_SHAPES` (`conference_talk`/`podcast_episode`/`tutorial`/`opinion_essay`/`research_summary`/`unknown`), used as-is and written back unchanged. If empty or typo'd, falls back to the rules classifier in `content_shape.py`. Metadata field `content_shape_source` records which path was taken. When the classifier returns `unknown`, triage skips the Notion write so a pre-populated override isn't stomped on the next tick. |
| `Name` (title) | When the user left Name blank, triage seeds it from the fetched page title (`fetch_url_meta`). When the user set a Name, triage leaves it untouched. Either way, Name is not persisted to the local store; `extract_complex_contents.published` later overwrites Name with `topic_card.extracted_title`. |

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
- `Podcast`
- `Other`

`PDF` is intentionally absent — `classify_content_type` never emits it; PDF
URLs fall through to `Article` and the fetcher's pdf handler claims them via
the registry catch-all. `Podcast` must now be present: audio-suffix URLs
(`.mp3` / `.m4a` / `.ogg` / `.wav` / `.opus`) are classified as Podcast, and
`podcast_canonicalize.py` may then substitute a YouTube URL on a map hit
(reclassifying the row to YouTube before it reaches the store). Without the
Podcast option the Notion API rejects Content Type writes for audio items
that don't hit the substitution map.

A `Content Shape` SELECT property is also required, with options:

- `conference_talk`
- `podcast_episode`
- `tutorial`
- `opinion_essay`
- `research_summary`

`unknown` is intentionally NOT a Notion option — when the rules classifier
can't pick a shape, triage skips the Notion write so pre-populated overrides
aren't stomped. If any of the five SELECT options above are missing on the
Notion DB, Notion's API rejects the `update_page` call and the run failure
sensor surfaces the error.

The Notion Queue DB's native `Status` property must additionally carry a
`Skipped` option (alongside `Queued` / `Fetching` / `Ready` / `Failed`).
Triage writes Status=Skipped on duplicate canonical_url detection;
without the option, the Notion API rejects the update.

The Queue DB also needs a `Use page body as content` **checkbox** property
(exact spelling — the sensor matches on this string in `sensors.py`).
When the user ticks it on a row, the sensor fetches the page's block
children, converts them to markdown via `notion_blocks.blocks_to_markdown`,
and writes the result into `queue_items.raw_content_override`. The
`fetched` asset then dispatches to the fetcher service's `/v1/structure`
endpoint instead of `/v1/fetch`. Default unset = false; rows without
the property tick fall through to the normal URL-fetch path.

## Runbooks

**Row stuck in Status=Queued forever:**
Most likely a `Status` Notion option mismatch — the DB doesn't have a
`Ready` or `Fetching` option. Open the DB schema in Notion
(`...` → Edit database → Status property) and add the missing option.

**Row ends at Status=Failed:**
Run failed → `mark_notion_failed_on_triage` wrote the traceback. Check
the Notion `Error` rich-text and `dagster job logs --run-id <id>`.

**Row ends at Status=Skipped:**
Duplicate detected → `triaged` wrote `Error="Duplicate of <other_page_id>"`.
The pointer is the original Notion page that already holds this
canonical_url; navigate to it for the real cohort. The duplicate row
was NOT written to `queue.db`. Skipped (not Failed) so the Notion view
can separate intentional dedup skips from real errors.

**Re-triage a row:**
Edit any field on the Notion row (or flip Status back to Queued) to bump
`last_edited_time`. The sensor uses `run_key=triage-{page_id}-{last_edited}` so
a bumped timestamp generates a fresh RunRequest on the next tick.

## DAG version

`TRIAGE_QUEUED_ITEMS_DAG_VERSION` in `orchestrators/config.py`.
Bump when DAG logic changes (asset graph topology, classification logic, routing
policy). Independent of the package version.
