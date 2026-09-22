# fetch_extract_queue

Sensor-driven pipeline that turns Notion-captured URLs into extracted Topic
Cards stored locally for newsletter-assistant to retrieve on engagement.

Picks up rows after triage_knowledge_queue has classified them: sensor filter is
Status=Fetching AND Content Type ∈ SUPPORTED_CONTENT_TYPES ({youtube, arxiv,
medium, facebook, github, file_pdf, file_audio, book_chapter, article, other}).
The fetcher service's handler registry routes the URL by host; the article
handler is a catch-all for anything not
yt/arxiv/medium/facebook/github/file_pdf/file_audio, so article and other reach
a real fetcher path. `book_chapter` never reaches `/v1/fetch` — its body is a
Notion `Source File` attachment, not a fetched URL (see `fetch_content` below).
Triage also registers the
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
   │                    │         │              └──► Notion: Status=Ready + Name (extracted_title,
   │                    │         │                   or the row's stored title for a
   │                    │         │                   SELF_DESCRIBING_TYPES row) + Description (core_mechanism)
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
   │                    └─ one OpenAI call over the fetched body, writing contributors_json /
   │                       publisher / unreadable_json on queue_items plus a
   │                       call_kind='metadata' extraction_calls row. A failed call is swallowed and
   │                       the asset still materialises — it does not block either branch below it.
   │                       A call that SUCCEEDS and reports the body does not stand on its own
   │                       (`stands_alone=false`) raises, failing the item into Notion with a
   │                       structured record (verdict/reason/action/causes) in the Error field.
   │
   └─ on failure (fetcher service returns problem+json or unreachable):
      run_failure_sensor → Notion: Status=Failed + Error

fetch_content ──► book_chapter_figures_described (blocking asset check)
                   a book_chapter whose figure anchors carry no description
                   parks at Status=Failed with a repair template, rather than
                   reaching extract_metadata looking complete. The operator
                   fills the template, attaches it as `Figure Text`, and flips
                   Status back to Queued; the next fetch injects the
                   descriptions and the check passes. Notion's Error carries the
                   check's `summary`, not Dagster's wrapper text
                   (`shared/run_failure`); the template stays in the run,
                   which Notion's 1,900-char Error could not hold.

Once the body is stored, `fetch_content` seeds Notion's Name from the fetched
title for a still-unnamed `SELF_DESCRIBING_TYPES` row, so a chapter parked on
the gate above is identifiable; best-effort, and never retried.

`fetch_content` calls the standalone `fetcher` service over dagster_network —
POST `/v1/fetch` for normal URLs, or, when the row carries a Notion `Source File`
attachment, POST `/v1/structure-oreilly` for an `.html`/`.htm` file or
POST `/v1/structure` for `.md`/`.txt`/`.markdown` — any other attachment
extension fails the item rather than being decoded as prose. A
`book_chapter` (an `ATTACHMENT_BODY_TYPES` content type) with no Source File
attached fails outright: its publisher answers an automated fetch with
Access Denied, so there is no URL fetch to fall back to. A description attached as `Figure Text`
replaces its figure anchor (`figures.inject_figure_descriptions`), which clears
the figure check below — it counts the anchors that remain. Injection sits after
the extraction floor and before the content hash, and every lane reads that one
body. For
`/v1/fetch`, the service is authoritative for source matching
(arxiv / youtube / medium / facebook / github / file_pdf / file_audio / article)
and quality-floor enforcement. `extract_metadata` asks the same service for one
extraction task over the fetched body before either branch below it, and is the
pipeline's second quality gate: the fetcher's floor is about size, this one is
about substance — a page that fetched 25k characters of navigation chrome clears
the floor and still carries nothing to extract from. `extract_reading_card` asks
for the remaining three tasks in one request. Neither runs a model in-process:
extraction moved to the service so this repo and newsletter-assistant stop
keeping their own copies of it, and the gate, the freshness check and the
`extraction_calls` ledger stay here, where the pipeline's state lives. fetch_content +
extract_reading_card include `content_preview` / `narrative_preview` / `topic_card_preview`
metadata (head + tail of the content) for at-a-glance verification.
```

Local store: `data/queue.db` (SQLite) for fetch + extraction state (raw_content,
extracted Topic Card, provenance, contributors/publisher/unreadable metadata,
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
# Bump the relevant TaskSpec.default_prompt_label in the fetcher service's
# extract/tasks.py AND add the new prompts/extraction/<label>.md file in the
# same commit, then re-launch:
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
  a `Source File` files-and-media property (the body for `book_chapter` rows,
  and an optional body override for any other type — see `fetch_content`
  above), a `Figure Text` files-and-media property (the JSON map of figure
  descriptions — see `figures.py`), and an `Error` rich-text property.
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
