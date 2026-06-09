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
   └─ on failure (fetcher service returns problem+json or unreachable):
      run_failure_sensor → Notion: Status=Failed + Error

`fetched` calls the standalone `fetcher` service (POST /v1/fetch) over
dagster_network; the service is authoritative for source matching
(article / arxiv / youtube) and quality-floor enforcement. `extracted`
runs ExtractorRegistry (ThreeCallOpenAIExtractor) in-process. fetched +
extracted include `content_preview` / `extraction_preview` metadata (head
+ tail of the content) for at-a-glance verification.
```

Local store: `data/queue.db` (SQLite). Lifecycle status (Queued / Fetching /
Ready / Failed) lives in Notion; everything else (raw_content, extracted
Topic Card, provenance) lives in the local store.

## URL → markdown — delegated to the fetcher service

URL→markdown is handled by `services/fetcher/` (a sidecar FastAPI
container). The orchestrator's `FetcherResource` is a thin httpx client
that POSTs `{url, quality: "fast", allow_paid, force_refresh: false}` to
`/v1/fetch` and maps the response onto `FetchResult`.

Error semantics: every non-200 problem+json becomes a `dg.Failure`; the
service's `problem.retryable` flag flows directly into `allow_retries`,
so transient upstream blips (502 UPSTREAM_FAILURE, 504 UPSTREAM_TIMEOUT,
429 RATE_LIMITED) re-queue under the asset's `RetryPolicy` while
permanent failures (400 BAD_URL, 422 UNSUPPORTED_SOURCE) fail fast and
surface to Notion as Status=Failed.

Required env: `FETCHER_URL` (base URL of the fetcher service). Optional:
`FETCHER_TIMEOUT_S` (default 60s), `FETCHER_ALLOW_PAID` (default `true`;
arxiv needs it to escalate from pymupdf to LlamaParse). LlamaParse /
SOCKS5 / proxy knobs live on the fetcher service as `FETCHER_*` envs —
see that service's README.

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
- **Fetcher service** — `FETCHER_URL` must point to a reachable
  `services/fetcher/` instance. In docker-compose the sidecar container
  resolves at `http://fetcher:8000` over `dagster_network`; for laptop
  `poe dagster-dev`, run `uv run uvicorn fetcher.app:app --workers 1
  --port 8000` from `services/fetcher/` and set
  `FETCHER_URL=http://localhost:8000`.
- **Notion AI training opt-out** — `Notion Workspace Settings → Notion AI
  → Manage data → "Don't use my workspace data to train models"` must be
  ON. Lifecycle-only Notion writes are intentional (raw content + Topic
  Cards stay in kp's local store).
