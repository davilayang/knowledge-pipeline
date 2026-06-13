# Changelog

All notable changes to this project will be documented in this file.

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)

## [Unreleased]

### Added

- **`content_shape` now classified at triage time and stored on `queue_items`.** New rules-only classifier `classify_content_shape` reads cached enrichment + URL → one of `conference_talk` / `podcast_episode` / `tutorial` / `opinion_essay` / `research_summary` / `unknown`. Seed rules in `conference_channels.yaml` / `youtube_channel_rules.yaml` / `article_host_rules.yaml`. `triaged` now depends on `enriched`. Notion property write and extractor prompt routing land in later phases.
- **`Content Shape` Notion SELECT now written by `triaged` and overridable by the user.** Sensor reads user-set value; classifier fires when empty / typo'd (mirrors `Content Type` semantics). `unknown` shape is left blank on Notion so pre-populated overrides aren't stomped. **Manual prep:** add `Content Shape` SELECT with options `conference_talk` / `podcast_episode` / `tutorial` / `opinion_essay` / `research_summary` to the Queue DB before deploy.

---

## [0.18.15] — 2026-06-13

### Added

- **`enriched` triage asset caches per-source URL signals for content-shape classification.** New asset runs in parallel with `triaged` per partition: YouTube oEmbed → channel + title, arXiv Atom API → title + abstract + categories, article → `fetch_url_meta` passthrough; result lands as `enrichment_json` on `queue_items`. Failure-tolerant — any HTTP error collapses to empty signals; never blocks triage. Phase 3 wires the cache into `classify_content_shape`.

---

## [0.18.14] — 2026-06-12

### Added

- **`queue.db` ready for content-shape extractor routing.** New `content_shape` + `enrichment_json` columns on `queue_items`, plus `upsert_enriched` / `get_content_shape` helpers in `domains.queue_store.sources`. Schema-only landing — classifier + asset wiring follow in later phases. Prod migration: `scripts/migrations/2026-06-12_queue_items_content_shape.sql`.

---

## [0.18.13] — 2026-06-12

### Changed

- **Re-triaging a Notion row now invalidates the prior cohort's fetched + extracted state.** `queue_store.upsert_triaged` ON CONFLICT clears `raw_content` / `fetched_*` / `extracted_*` cohort fields and deletes the row's `extraction_calls`. Previously a re-queue against a row with cached `raw_content` short-circuited the `fetched` cache check, running extraction on stale content — the symptom that forced a manual `DELETE FROM queue_items` after the PR #109 Medium-handler fix.
- **`published` now overwrites Notion `Name` with `topic_card.extracted_title`** alongside the existing `Description` write — the LLM's title is materially sharper than the trafilatura HTML meta triage seeded ("Welcome to Medium" → "Why I Quit My Job at Google"). `NotionQueueResource.update_status` gains an optional `name` parameter with the same strip-and-skip-on-empty discipline as `description`.

---

## [0.18.12] — 2026-06-12

### Changed

- **Medium URLs now route through the medium handler in prod.** `medium_domains.yaml` moved into the package at `src/fetcher/data/`, loaded via a `Path(__file__)`-relative default — ships with the wheel, no Dockerfile data COPY or env var. `_load_domains` fails fast on missing/empty file so the next regression surfaces at fetcher startup, not silently.
- **Fetcher `tier_log` now carries per-tier diagnostic detail.** `TierLogEntry` gains `duration_ms` / `floor` / `error_kind` / `detail`; handlers populate `RawTierResult.detail` with 4xx body slices, exception text, and upstream reasons (jina 401, curl_cffi failures, tavily/rapidapi/arxiv errors). Cache reads stay back-compat via `.get(...)` defaults.
- **Fetched-asset metadata now includes `canonical_url`** — the cross-repo `kp_queue_cache` lookup key was previously invisible in the Dagster materialization view.
- **`canonicalize_url` renamed to `normalize_url`** in `triage_queued_items/classify.py`, matching NA's function name so the byte-for-byte parity contract is self-documenting. Disambiguates from the fetcher service's HEAD-follow `canonicalize()`. DB column `queue_items.canonical_url` keeps its name.

---

## [0.18.11] — 2026-06-11

### Changed

- **arXiv URLs canonicalise to `/abs/<id>` at triage time.** `canonicalize_url` previously passed arXiv URLs through unchanged, leaving every arXiv row's `queue_items.canonical_url` mismatched against NA's `normalize_url` — silent NA→kp `kp_queue_cache` miss since arXiv ingestion existed. Now collapses `abs`/`pdf`/`html`/bare-ID forms to `<scheme>://<netloc>/abs/<id>` (version stripped), mirroring NA's existing abs/pdf canonicalisation. The `html/` extension is one-sided pending a coordinated NA-side change to `is_arxiv_url`.

---

## [0.18.10] — 2026-06-11

### Changed

- **arXiv URLs at `arxiv.org/html/<id>` now route through the arxiv fetcher handler.** The handler previously matched only `abs/` and `pdf/` path prefixes; `html/`-form URLs fell through to the generic article handler and surfaced as ``no handler matches URL``. The handler still resolves the canonical PDF via the metadata API, so the html surface is purely a new entry point — same downstream extraction.

---

## [0.18.9] — 2026-06-11

### Changed

- **Backup pipeline's `storage_capacity` asset now surfaces rclone's stderr in the Dagster failure.** Previously `subprocess.run(..., capture_output=True, check=True)` swallowed stderr — `rclone about gdrive:` failures (expired OAuth, missing remote, network) appeared only as `CalledProcessError` with no diagnostic. Now raises `dg.Failure` with stderr in description + metadata (`remote`, `exit_code`, `stderr`).

---

## [0.18.8] — 2026-06-11

### Changed

- **Three env vars must be renamed in your deploy `.env`.** `BACKUP_SOURCE_DIR` → `BACKUP_SRC_DIR`, `BACKUP_DIR` → `BACKUP_DST_DIR` (backup pipeline), and `FETCHER_DEFAULT_TIMEOUT_S` → `FETCHER_UPSTREAM_TIMEOUT_S` (fetcher service). Old names will cause a failed run init or silent misconfiguration.
- **Four env vars can be removed from your deploy `.env`.** `OPENAI_EMBEDDING_MODEL`, `OPENAI_EMBEDDING_DIMS`, `VECTOR_STORE_MAX_PER_TICK`, and `WIKI_MAX_PER_TICK` are now code constants in `def_config.py`; they are coupled to existing Chroma vectors and do not vary per deploy.

---

## [0.18.7] — 2026-06-11

### Changed

- **Extract pipeline guard failures now include a clickable Notion page URL.** Both failure paths — missing `queue_items` row and missing Content Type — surface the Notion URL as a `dg.MetadataValue.url` link in the Dagster UI event and as plain text in container logs and the Notion row's Error column, cutting triage time from "which partition failed?" to one click.
- **Poll sensor logs `queue_db_id` on every tick.** The configured Notion database ID appears in Dozzle logs at the start of each sensor evaluation, making `NOTION_QUEUE_DB_ID` misroutes visible immediately rather than hours into a silent incident.

---

## [0.18.6] — 2026-06-10

### Added

- **Podcast audio URLs are now auto-substituted to YouTube at triage time.** When a queued item resolves to an audio file (`.mp3`, `.m4a`, etc.) and the show is in `podcast_youtube_map.yaml`, the triage asset fetches the show's YouTube playlist feed, fuzzy-matches the episode title, and replaces the canonical URL with the matched YouTube URL — routing the item through the YouTube fetcher for a free transcript instead of Whisper.

### Changed

- **`classify_content_type` now emits `"Podcast"` for direct audio file URLs.** Previously those fell through to `"Article"`; they now get a distinct type so the substitution step can intercept them.

---

## [0.18.5] — 2026-06-10

### Added

- **`POST /v1/structure` on the fetcher service** turns noisy user-pasted article bodies into clean markdown via a trafilatura → conservative passthrough → cloud LLM cascade (Ollama Cloud primary, OpenAI fallback). Chain config: `services/fetcher/config/structurer.yaml`; prompt: `services/fetcher/prompts/structure_v1.md`; new envs `FETCHER_OPENAI_API_KEY` / `FETCHER_OLLAMA_API_KEY` (at least one required for the cloud stage).
- **Ticking Notion's `Use page body as content` checkbox routes the row's pasted body through `/v1/structure` instead of fetching the URL.** Sensor reads the checkbox, converts block children via `notion_blocks.blocks_to_markdown`, and threads the result through new `queue_items.raw_content_override` column. Requires the one-time `scripts/migrations/2026-06-10_queue_items_raw_content_override.sql` against prod queue.db before deploy.

### Changed

- **Triage now routes every supported URL to `Status=Fetching` — the Tier A / Tier B split is gone.** Article and Other rows that previously stopped at `Status=Ready` for NA-at-engagement now flow through `extract_complex_contents.fetched` against the fetcher's article handler (catch-all). `SUPPORTED_CONTENT_TYPES` widened to `{YouTube, arXiv, Article, Other}`; `is_tier_a()` and `_TIER_A` removed.
- **`FetcherResource` no longer tunnels orchestrator → fetcher calls through `HTTP(S)_PROXY`.** `httpx.Client(trust_env=False)` keeps the internal service-to-service call off the upstream proxy meant for the fetcher's own egress.

---

## [0.18.4] — 2026-06-10

### Added

- **Medium articles now fetch via a dedicated handler.** Jina free tier, then mediumapi.com RapidAPI paywall bypass under optional `FETCHER_RAPIDAPI_KEY`. Domain set in `services/fetcher/config/medium_domains.yaml`; path overridable via `FETCHER_MEDIUM_DOMAINS_PATH`.
- **Generic PDF URLs now route to a dedicated `pdf` handler.** Free tier: pymupdf4llm with a 50 MB download cap. Paid tier: LlamaParse via `FETCHER_LLAMA_PARSE_TIER_PDF` (defaults to `fast`). arXiv PDFs still go to the arxiv handler.
- **Article handler gains a paid Tavily Extract tier.** Runs when Jina and curl_cffi+trafilatura both fail validation. `FETCHER_TAVILY_API_KEY` is optional; tier is unreachable when unset.

### Changed

- **Fetcher returns 502 `UPSTREAM_FAILURE` when no tier produces clean content.** Article and Medium tiers run output through `is_valid_content` + `is_likely_truncated` (`services/fetcher/src/fetcher/validator.py`) — paywall fragments, JS walls, Cloudflare challenges, and truncated bodies no longer surface as silent 200s.
- **Jina requests now carry `X-Return-Format: markdown` and `X-Timeout: 20`** so the response shape is explicit and Jina enforces its own timeout below the httpx client's.
- **`CLAUDE.md` @-imports now resolve again** — directory renamed `personal-knowledge-os/` → `knowledge-os/` upstream (data-context-builder PR #47). Updated absolute paths plus a stray test-fixture tag in `tests/domains/queue_store/test_sources.py`.

---

## [0.18.3] — 2026-06-09

### Changed

- **Fetcher service wire vocabulary now uses `kind`.** `/v1/fetch` response field `source_type` → `kind`; `/healthz` field `registered_sources` → `registered_kinds`; error code `UNSUPPORTED_SOURCE` → `UNSUPPORTED_KIND`. Internal directory `services/fetcher/src/fetcher/sources/` → `handlers/` reflects that each module handles a URL kind. Cache schema column unchanged.

---

## [0.18.2] — 2026-06-09

### Changed

- **Fetcher service: extraction primitives moved from `services/fetcher/src/fetcher/parsers/` to `extractors/`.** Internal rename; the directory mixes local parsers (`trafilatura`, `pymupdf`) with remote-API callers (`llamaparse`, `oembed`, `youtube_transcript`), and the new name describes both honestly. No consumer impact — the service is HTTP-only.

---

## [0.18.1] — 2026-06-09

### Changed

- **`extract_complex_contents/fetched` now delegates all URL→markdown work to the fetcher service.** `FetcherResource` is a thin httpx client to `POST /v1/fetch`; per-tier in-process fetchers (curl_cffi+trafilatura, pymupdf+LlamaParse, youtube-transcript-api) are removed. RFC 7807 `retryable` flows into Dagster `allow_retries`, so transient blips re-queue and permanent failures (bad URL, unsupported source) fail fast.

- **Env contract updated for `extract_complex_contents`.** Three new required vars: `FETCHER_URL`, `FETCHER_TIMEOUT_S`, `FETCHER_ALLOW_PAID`. Removes `PI_SOCKS5_URL`, `EXTRACT_QUEUE_IMPERSONATE_PROFILE`, `YOUTUBE_PROXY_URL`, `LLAMA_CLOUD_API_KEY`, `LLAMA_PARSE_TIER` — those moved into the fetcher service in 0.18.0. `EXTRACT_COMPLEX_CONTENTS_DAG_VERSION` bumped 2→3; previously-materialized `fetched` partitions show stale on first deploy and refill on the next scheduled tick.

- **Extraction floor lowered from 2000 → 500 chars.** The fetcher cascade can return sub-floor content via its `best_result` fallback; 500 rejects degenerate fetches while letting short legitimate YouTube clips through.

- **`fetched` Dagster UI compute_kind changed from `python` to `http`.** Matches the system-not-tool convention used by `extracted` (`openai`) and `published` (`notion`).

- **Fetcher service now emits its own INFO logs.** `logging.basicConfig(force=True)` in `create_app()` overrides uvicorn's pre-installed root logger config so `fetcher.*` namespace lines surface.

### Added

- **`make dev` starts the full laptop dev stack in one shot** — data services, fetcher service, and Dagster UI. `poe fetcher-dev` and `make fetcher-dev` also available when only the fetcher needs to be run independently.

### Removed

- **`extract_complex_contents/fetchers/` package deleted** (`article.py`, `arxiv.py`, `youtube.py`, `result.py`). `curl-cffi`, `youtube-transcript-api`, `pysocks`, and `arxiv` dropped from `packages/orchestrators` dependencies.

---

## [0.18.0] — 2026-06-09

### Added

- **Fetcher service** at `services/fetcher/` — URL → markdown for KP and NA, replacing the per-pipeline fetchers. Three sources today (article via Jina → curl_cffi+trafilatura; arxiv via pymupdf4llm → LlamaParse agentic_plus, strict; youtube via transcript-api with oEmbed header). Endpoints: `POST /v1/fetch` (sync, ETag / 304), `POST/GET/DELETE /v1/fetches` (async batch with real in-process cancellation), `GET /v1/canonicalize`.

- **`domains.fetches_store.sources`** owns all fetcher SQLite state. New named ops (`cache_lookup/upsert`, `insert_job`, `update_job`, `get_job`, `get_job_status`, `canonicalize_lookup/upsert`, `mark_orphans_failed`) match the `queue_store`/`raw_store` convention — `_connect` stays private, callers never see SQL or connections.

- **Free-first tier cascade.** Each source declares ordered free → paid tiers; cascade walks free tiers first, escalates to paid only when `allow_paid=true` AND the quality floor isn't met. Paid escalations (LlamaParse `agentic_plus`) gated behind explicit caller intent.

- **`kos-network` external Docker network** shared with sibling stacks. Fetcher reachable as `http://fetcher:8000` from KP containers and `http://kp-fetcher:8000` from other KOS stacks. `scripts/deploy-hcloud.sh setup` creates the network on first deploy.

- **Single-worker invariant.** `uvicorn --workers 1` is load-bearing: per-source `asyncio.Semaphore`, per-URL `asyncio.Lock`, and `task_registry: dict[job_id, asyncio.Task]` are correctness primitives. README banner documents the failure modes if this is violated.

- **RFC 7807 error contract** at `endpoints/errors.py` with `retryable` field. Exception hierarchy (`BadUrl`, `UnsupportedSource`, `UpstreamFailure`, `UpstreamTimeout`, `RateLimited`) split across `errors.py` (domain) and `problems.py` (pure body factory, no FastAPI) so workers and cache can persist the same problem shape into SQLite.

- **Soft-404 guard on article tiers.** Jina Reader wraps upstream 4xx/5xx in HTTP 200 with a `"Warning: Target URL returned error"` marker; the guard now demotes these to empty content. curl_cffi short-circuits on HTTP status ≥ 400 before invoking trafilatura. Prevents error pages from being cached as article text.

### Changed

- **`FETCHER_JINA_API_KEY` is now optional.** Jina Reader's free tier works without auth at lower rate limits; the key only unlocks the paid quota. `Authorization` header is set only when a key is configured. Required envs that remain: `FETCHER_SOCKS5_URL`, `FETCHER_LLAMA_PARSE_API_KEY`.

---

## [0.17.3] — 2026-06-08

### Changed

- Use TRUNCATE policy after queue db database write to ensure checkpoints are merged into main db. Allow reader can read full data. 

## [0.17.2] — 2026-06-06

### Changed

- **`TopicCardScorer` scores `extracted_title` via embedding similarity, not exact match.** First real benchmark run revealed exact-match on free-text titles was pure noise — LLMs almost never reproduce a hand-written reference verbatim, dragging `__overall__` down by ~0.13. `ExactMatchJudge` remains for future tag-like fields.

---

## [0.17.1] — 2026-06-06

### Added

- **`evals.extraction` harness** consuming the `evals.core` substrate — `make_three_call_variant`, `TopicCardScorer`, `run_variant`s, `run_benchmark`.
- **`eval-extraction` CLI** with `--dry-run` pre-flight cost estimate; live mode redirects to the workbench notebooks until a variant registry lands.
- **Three workbench notebooks** (`ab_topic_card` / `ab_narrative` / `ab_followups`) plus a 9-cell `_template`, all jupytext py:percent paired.
- **`poe nb-sync` and `poe nb-run`** for jupytext + papermill workflows; `poe jupyter` now roots at `packages/evals/notebooks/`.
- **`datasets/extraction_eval.jsonl`** — 15-row hand-curated synthetic fixture set (5 per content type) with measurement-floor caveat documented in `datasets/README.md`.

### Changed

- **Extractor variants now work inside Jupyter kernels.** `make_three_call_variant` thread-hops `ThreeCallOpenAIExtractor.extract()` when a running event loop is detected, avoiding the prior `asyncio.run()` `RuntimeError`.

---

## [0.17.0] — 2026-06-06

### Added

- **`evals.core` substrate** — pure-function primitives composing into per-pipeline harnesses in Steps 3+. Variant identity hashing over `(config, provenance)`; schema-versioned JSONL fixtures (`SchemaVersionMismatch` on drift); JSON-safe `snapshot()` with sentinels for LangGraph state.
- **Run persistence** at `data/eval_runs/{kind}/{target}/{version}/{run_id}/run.json` — workbench (30d retention) vs benchmark (indefinite) split via `kind`.
- **Four judges** with injected callables — `ExactMatchJudge`, `EmbeddingSimilarityJudge`, `LLMJudge`, plus `JudgeProtocol`. No provider deps in the substrate.
- **`CostBudget` + `BudgetExceededError`** — pre-launch spend gate; harness aborts before $$.
- **Field-level `DiffReport`** with text + HTML renderers for variant comparison.
- **Eval composition patterns documented** in `packages/evals/README.md` — adding a content type, upgrading a prompt, composing a workflow graph, golden-regression migration.

---

## [0.16.0] — 2026-06-06

### Changed

- **Extractor classes now live in `workflows.extraction`.** `ThreeCallOpenAIExtractor`, `ExtractorProtocol`, and `ExtractionUsage` move out of `orchestrators`; production import path updated.
- **Extraction prompts moved to repo-root `prompts/extraction/`.** `KP_PROMPTS_ROOT` env var overrides the default root for evals and tests.

### Removed

- **`SingleShotOpenAIExtractor` deleted** — unused since the three-call cutover.

---

## [0.15.5] — 2026-06-06

### Changed

- **Notion `Error` column shows the actual step failure reason** instead of `Steps failed: [...]`. New shared helper `defs/shared/run_failure.py` reads the terminal `dg.Failure(description=...)`.
- **Extract retries are now transient-only.** `FetchResult.transient` (set by arXiv on 5xx/connection, YouTube on IP/request blocks) gates `allow_retries`; asset-level `RetryPolicy(max_retries=1, delay=120)` replaces the in-fetcher 60s tenacity loop (now 15s).

---

## [0.15.4] — 2026-06-04

### Changed

- **Triage duplicate-detection Error in Notion is now clickable.** Was: `"Duplicate of <bare-uuid>"`. Now: `"Duplicate of <Name>" — <canonical_url>`, where `<Name>` links to the original Notion page and `<canonical_url>` is itself a hyperlink. Adds `NotionQueueResource.get_page_name()` (one extra Notion read per duplicate) and reshapes `update_status_skipped(page_id, segments)` to accept structured rich_text tuples instead of a plain string.

---

## [0.15.3] — 2026-06-04

### Changed

- **Asset graph now shows the stores each pipeline reads.** Added `research_store`, `queue_store`, and `notion_queue` `AssetSpec` anchors in `upstream_sources.py`. `snapshot_research` / `snapshot_queue` (backup) and `triage_queued_items/triaged` declare `deps=` on the relevant anchors. Existing `sessions` anchor renamed to `session_store` for SQLite-store naming consistency (`raw_store` / `queue_store` / `session_store`). Pure graph-rendering change — no compute logic shifts.

---

## [0.15.2] — 2026-06-03

### Changed

- **arXiv fetcher now survives transient upstream overload.** Retry budget extended 15s → 60s; `ConnectionError` added alongside `HTTPError` so 503/429 spells from `export.arxiv.org` no longer surface as run failures.
- **Per-pipeline concurrency keys now actually enforce serialisation.** Added `concurrency.pools` (`granularity: op`, `default_limit: 1`) to `dagster.yaml`; previously the existing op-tag keys were metadata-only and parallel runs could race upstream APIs.

---

## [0.15.1] — 2026-06-03

### Changed

- **Triage dedupes Notion captures by `canonical_url`** — a second capture of an already-queued URL → `Status=Skipped`, `Error="Duplicate of <other_page_id>"`; no queue.db pollution. **Deploy:** add a `Skipped` option to the Notion Queue's Status property before merge.

---

## [0.15.0] — 2026-06-03

### Changed

- **Extraction is now three focused LLM calls per item** (narrative markdown + structured `TopicCard` + structured `Followups`) replacing the single monolithic call. Calls 2+3 fire in parallel and hit OpenAI's prefix cache within sub-seconds of call 1. Storage moves to an `extraction_calls` table (one row per call, mirrors NA's `core_llm_calls` shape); the legacy single-shot columns are dropped.
- **Prompt labels moved from env vars to code constants** — `EXTRACT_QUEUE_PROMPT_LABEL_{ARTICLE,YOUTUBE,ARXIV}` removed; no replacement env vars needed. **Deploy:** remove those three vars from `.env` / `.env.deploy`, run `scripts/migrate_extraction_to_calls_table.py` on prod `queue.db`, then re-trigger `extract_complex_contents/extracted` on the ~7 already-extracted partitions (~$0.20–0.50 OpenAI spend).

---

## [0.14.9] — 2026-06-03

### Changed

- **Notion Queue `Canonical URL` flipped URL-type → Text-type.** With two URL properties on the DB, Web Clipper / Save-to-Notion silently routed mobile captures to `Canonical URL` instead of `URL`, stranding rows at `Status=Queued`. Text-typing leaves only one URL-type property → unambiguous capture. **Deploy:** flip property type in Notion UI at merge. (`shared/queue_resources.py`)
- **Triage backfills `Added At` from `created_time` when missing.** Mobile captures often omit it; sensor now writes `created_time` through; existing values preserved. (`triage_queued_items/sensors.py`, `assets.py`)

---

## [0.14.8] — 2026-06-03

### Changed

- **`canonical_url` matches newsletter-assistant's `normalize_url`** — kp was emitting a different shape than NA's lookup form, so NA's `kp_queue_cache` tier silently missed on the `www.` / `youtu.be` / `?si=` axes. Now mirrors NA: keep only `v=` on `youtube.com`/`m.`/`music.`; strip query+fragment everywhere else. (`triage_queued_items/classify.py`)
- **Triage writes `Canonical URL` to Notion** — new URL-type property on the Queue DB, set in triage's first properties update. (`shared/queue_resources.py`, `triage_queued_items/assets.py`)
- **Deploy action required:** backfill prod `queue.db` on Hetzner with the new canonical shape (commands in PR #75 body). Existing rows have stale `canonical_url`; new triage runs are correct without intervention.

---

## [0.14.7] — 2026-06-03

### Changed

- **LlamaParse tier promoted to required `LLAMA_PARSE_TIER` env var** — was hardcoded as `agentic_plus` (LlamaCloud's most expensive tier), forcing dev iteration to burn prod-grade credits. Dev now sets `LLAMA_PARSE_TIER=fast` (layout-only, ~100× cheaper); prod sets `LLAMA_PARSE_TIER=agentic_plus`. Field renamed `llama_parse_tier_arxiv` → `llama_parse_tier`. **Deploy action required:** set the var in `.env` and `.env.deploy` before next deploy — unset → run init fails fast. (`extract_complex_contents/resources.py`, `.env.example`)

---

## [0.14.6] — 2026-06-02

### Changed

- queue.db access module moved from `domains.raw_store.queue` to `domains.queue_store.sources` — one storage backend per `domains/<store>/` module, matching the rest of the per-store layout. Public function signatures and the `queue.db` schema are unchanged.

---

## [0.14.5] — 2026-06-02

### Changed

- **YouTube transcript fetcher now accepts `socks5://` proxy URLs** — `pysocks` added to `knowledge-orchestrators` deps (`packages/orchestrators/pyproject.toml`). Previously, setting `YOUTUBE_PROXY_URL=socks5://…` raised `InvalidSchema: Missing dependencies for SOCKS support` in `requests`; now it routes correctly. (`curl_cffi`-based article fetcher is unaffected — libcurl bundles its own SOCKS support.)

---

## [0.14.4] — 2026-06-02

### Added

- **Daily backup of `queue.db`** (kp's triage + extract queue) — disk loss no longer means re-classifying and re-extracting every queued item. `queue.db` is sourced from kp's own `data/` directory rather than `BACKUP_SOURCE_DIR`. (`backup_readings/assets.py`, `backup_readings/checks.py`)

---

## [0.14.3] — 2026-06-02

### Changed

- **Extract pipeline now writes `core_mechanism` to Notion's Description property for Tier A items** — after a successful extraction, the published asset overwrites the triage-time HTML meta with the model-extracted `core_mechanism` field. (`extract_complex_contents/assets.py`, `shared/queue_resources.py`)

---

## [0.14.2] — 2026-06-02

### Changed

- **Notion `Status` property migrated from `select` → native `status` type** — queue reads and writes now use `{"status": {"name": …}}` instead of `{"select": {"name": …}}`, matching the Notion schema after its native-status migration. Without this, the pipeline would silently stop processing the queue (triage sensor finds zero rows, extract sensor finds zero rows). Both `query_for_triage` and `query_for_extract` filters updated; `write_triaged`, `update_status`, and `update_status_failed` all write the new type. (`shared/queue_resources.py`)
- **Whitespace stripped from Notion title and description writes** — incoming HTML metadata may carry trailing newlines or padding; both fields are now `.strip()`-ed before writing to Notion. A value that strips to empty is treated as absent (no blank overwrite). (`NotionQueueResource.write_triaged`)

---

## [0.14.1] — 2026-06-02

### Added

- **URL enrichment at triage** — `triaged` now follows redirects and extracts page title + short description (≤ 200 chars) from HTML head before classifying. Notion's `Name` is seeded from the fetched title when the user left it blank; `Description` is always written when one was found. Implementation: `triage_queued_items/url_meta.py` (`fetch_url_meta`, backed by `httpx` + `trafilatura`; never raises — empty meta on any network or parse failure).

### Changed

- **Triage Notion write now includes `Name` + `Description` fields** — `TriageNotionResource.write_triaged` accepts optional `name` and `description`; name is skipped when the user already set one, description is always overwritten. `final_url`, `fetched_title`, and `fetched_description` added to asset materialization metadata.

---

## [0.14.0] — 2026-06-01

### Added

- **YouTube transcript fetcher** — `youtube-transcript-api` + oEmbed; optional `YOUTUBE_PROXY_URL` for IP-blocked hosts.
- **arXiv fetcher** — `arxiv` PyPI + LlamaParse on `agentic_plus` tier (hard-fail). New env `LLAMA_CLOUD_API_KEY`.
- **Per-type prompt labels** — `EXTRACT_QUEUE_PROMPT_LABEL_{ARTICLE,YOUTUBE,ARXIV}` replace singular `EXTRACT_QUEUE_PROMPT_LABEL`.
- **Head + tail content previews** on `fetched` + `extracted` (500+500 chars) in MaterializeResult metadata.
- **Per-pipeline run-failure sensors** — `mark_notion_failed_on_{triage,extract}` write `Status=Failed` + error to Notion.

### Changed

- **Pipeline split** — single `extract_queued_items` → `triage_queued_items` + `extract_complex_contents` coordinated via Notion `Status` + `Content Type` ("Notion-as-bus"). Shared `queue_items` dynamic partition in `defs/shared/partitions.py`.
- **Triage: single `triaged` asset** (was `classified` + `routed`). Notion-set `Content Type` overrides URL classifier; typo/empty → classifier fallback. `Name` passthrough as metadata; triage never writes it.
- **Extract: 6 branched assets → 3** (`fetched` → `extracted` → `published`). Per-type dispatch in `FetcherResource` + `ExtractorRegistry`; `published` isolates Notion write so a Notion hiccup doesn't re-spend OpenAI.
- **Typed `dg.Config` for asset inputs** (was stringly-typed tags). Pydantic validates at launch; manual UI launches use the Launchpad config form.
- **`queue_items` schema** — single `extraction_payload` JSON column + `canonical_url` + `content_type`. Idempotent `ALTER TABLE` upgrade for existing DBs. `get_queue_extraction()` consumer API preserved.
- **Extraction via OpenAI + `ExtractorRegistry` strategy** (`extractors/` subfolder mirrors `fetchers/`). v1: `SingleShotOpenAIExtractor`; future per-type swaps drop in as one file + one registry line. `anthropic` dep dropped, `openai` added.
- **Sensor names symmetric** — `poll_notion_for_<stage>` + `mark_notion_failed_on_<stage>` across both pipelines.

### Removed

- **`TitleFetcherResource`** — `<title>` tag was SEO junk on most sites; downstream extract LLM / NA-at-engagement fills `Name` from real content.
- **`pymupdf4llm`** dep — arXiv uses LlamaParse exclusively.

---

## [0.13.0] — 2026-06-01

### Added

- **`extract_queued_items` pipeline** — turns Notion-captured URLs into extracted Topic Cards. Sensor polls Notion queue every 15 min (matches `Status=Queued` and rows with empty Status — mobile Share Sheet bypasses Notion templates on the free tier); per URL: Jina → curl-cffi/Pi SOCKS5 fallback → Anthropic Topic Card → flips Notion `Status=Ready`. Failures mirror back as `Status=Failed`. New `orchestrators` deps: `notion-client`, `curl-cffi`, `trafilatura`, `anthropic`. See `packages/orchestrators/src/orchestrators/defs/extract_queued_items/README.md` for envs + DB setup.

- **`queue_items` SQLite table + `get_queue_extraction()` consumer API** (`data/queue.db`, `domains.raw_store.queue`). Notion holds lifecycle status + URL only; all extracted content stays kp-local per the 2026-05-27 privacy decision. `get_queue_extraction(notion_page_id=...)` is the cross-repo read path newsletter-assistant uses on engagement.

---

## [0.12.4] — 2026-05-17

### Added

- **OS-wide framing `@`-imported into `CLAUDE.md`** (`~/GitHub/data-context-builder/documents/personal-knowledge-os/framing.md`). Previously only the local trajectory was imported — sessions now see the canonical exit-ramps + decision rubric alongside this repo's trajectory.

### Changed

- **Per-repo trajectory `@`-import now points at the personal-knowledge-OS hub** (`~/GitHub/data-context-builder/documents/personal-knowledge-os/trajectories/knowledge-pipeline.md`) rather than the local `docs/concept/personal-knowledge-os.md`, which has been removed. Both the OS-wide framing and this repo's trajectory now live in the hub. Trajectory updates land as hub PRs, derived from this `CHANGELOG.md`.

---

## [0.12.3] — 2026-05-15

### Changed

- **`populate_vector_store` schedule now fires successfully on HH:30 ticks.** Cron is `*/30 * * * *` but assets were partitioned hourly — HH:30 ticks died with `DagsterUnknownPartitionError`. Partitions are now a 30-min `TimeWindowPartitionsDefinition` aligned to the cron (`def_config.py`); `POPULATE_VECTOR_STORE_DAG_VERSION` → `"2"`.

---

## [0.12.2] — 2026-05-14

### Changed

- **`dagster-code` image now creates `/opt/dagster/home/storage` with `dagster` ownership at build time.** Pipelines failed in production with `PermissionError: [Errno 13] Permission denied: '/opt/dagster'` because the daemon/webserver send their `DAGSTER_HOME`-derived fs_io_manager path over gRPC to dagster-code, whose image never created `/opt/dagster`. The path now exists in the user-code image with the right owner; asset outputs persist successfully. Container-layer storage (not a volume) — IO outputs are re-materializable on rebuild, matching default fs_io_manager semantics.

- **`.env.example` no longer hard-codes `APP_UID=1001` / `APP_GID=1001`.** The previous comment claimed `1001` was "fine for first-time setup," but Hetzner / stock Ubuntu hosts have their first non-root user at uid 1000, causing bind-mount ownership mismatches at deploy time. New placeholder is `1000` and the comment is now a MUST-do instruction to verify with `id -u` / `id -g` on the deploy host before the first `docker compose --profile app build`.

---

## [0.12.1] — 2026-05-14

### Changed

- **`wiki/aliases_index` now self-maps every page entity_id**, even for entities with zero rows in `wiki.aliases`. Previously such entities were absent from `data/wiki/_index/aliases.json` — the consumer agent's lookup would silently return `None` and fall through to vector recall, masking the wiki page. Fix: a new loop over `wiki.pages` registers `entity_id → entity_id` before the alias and canonical-name loops; idempotent under the existing collision check.

---

## [0.12.0] — 2026-05-14

### Added

- **Three new YAML frontmatter fields on every wiki page** — `summary` (one-sentence LLM-generated description, shape-word-free), `aliases` (list of alternate entity names sourced from `wiki.aliases` in Postgres, not from the LLM), and `num_sources` (integer count of distinct source content_ids). Downstream consumers (e.g. newsletter-assistant) can now "peek before fetch" without loading the full page body. Fields written in `domains/wiki/io.py`; aliases + source count fetched via two new helpers in `domains/wiki/state.py`.

- **Alias index sidecar at `data/wiki/_index/aliases.json`** — flat `{lowercased_alias_or_canonical_title → entity_id}` map rebuilt at the end of every `synthesize_wiki` tick by the new `wiki/aliases_index` Dagster asset. Atomic write (tmp + rename); skipped when byte-identical; raises on collision. Enables O(1) entity resolution for sibling agents.

### Changed

- **LLM page-output parsing is now whitelist-only** — only documented fields are accepted; hallucinated `aliases` / `num_sources` from the model are silently dropped. Falls back to first sentence of body when `summary` is missing or empty. Logic lives in `workflows/wiki_synthesis/parsing.py`.

- **`SYNTHESIZE_WIKI_DAG_VERSION` bumped 3 → 4** — new `wiki/aliases_index` asset changes DAG topology.

---

## [0.11.3] — 2026-05-14

### Changed

- **Heading-aware embeddings for markdown content.** Markdown-chunked items (raw_store, notes, research) now embed with their heading breadcrumb prepended (e.g. `"Introduction > Setup\n\nactual chunk text..."`), so retrieval better ranks chunks within their containing section. The stored `document` field in Chroma stays unchanged — only the embedded vector encodes the breadcrumb. Heading metadata also lands in Chroma as `heading_path` for all sources, including time-range strings for `turn_grouping` (sessions). No re-embed needed for downstream consumers; existing chunks remain searchable, new ingests include the new field.

---

## [0.11.2] — 2026-05-14

### Added

- **Chroma service in docker-compose** as `chroma` (image `chromadb/chroma:1.5.5`, loopback-bound port `127.0.0.1:8000`, named `chroma_data` volume). The `populate_vector_store` pipeline now has a server to point at on the deployed host; `dagster-code`'s `environment:` overrides `CHROMA_HOST=chroma` so the compose-internal network name takes precedence over `.env`. Pipeline schedule remains paused — turning it on is the next phase.

- **Compose profiles for local-dev split.** Services are now grouped into `data` (postgres + chroma) and `app` (dagster-code + dagster-webserver + dagster-daemon). Data services hold both profiles so `--profile app` resolves `depends_on` links and `--profile data` starts them standalone. `poe dagster-dev` now owns the data-services lifecycle: it brings postgres + chroma up at start and tears them down on exit (Ctrl+C, crash, normal end) via a bash EXIT/INT/TERM trap. Production deploy script (`scripts/deploy-hcloud.sh`) now invokes `docker compose --profile app`. **Bare `docker compose up` now starts nothing** — every service is profile-gated; use `--profile app` (full stack) or `--profile data` (deps only).

---

## [0.11.1] — 2026-05-14

### Changed

- **Code-location module path shortened.** Pipelines now live directly under `orchestrators.defs.<name>` (was `orchestrators.defs.pipelines.<name>`); the production Docker CMD and `poe` tasks load `orchestrators.definitions` as the single code-location entry point (was `orchestrators.defs.pipelines.definitions`). Pure module-path refactor — asset names, partitioning, schedules, and resources unchanged.

---

## [0.11.0] — 2026-05-13

### Added

- **`populate_vector_store` pipeline embeds raw_store, notes, sessions, and research into ChromaDB** via OpenAI `text-embedding-3-small` (1536 dims). Four collections are written: `contents`, `notes`, `conversations`, `research_documents`. Metadata per chunk includes `content_id`, `chunk_index`, `_embedding_model`, `_embedding_dims`, plus optional fields (`title`, `author`, `content_date`, `url`, `started_at`, `source_ref`). Pipeline launches paused (`STOPPED`) — manual trigger only for now.

- **New required env vars `CHROMA_HOST` and `CHROMA_PORT`** to connect the pipeline to ChromaDB; unset → run init fails fast. Optional tuning knobs: `OPENAI_EMBEDDING_MODEL` (default `text-embedding-3-small`), `OPENAI_EMBEDDING_DIMS` (default `1536`), `VECTOR_STORE_MAX_PER_TICK` (default `50`), `VECTOR_STORE_INGEST_CONCURRENCY` (default `4`). All documented in `.env.example`.

---

## [0.10.0] — 2026-05-12

### Changed

- **`VectorStoreResource` is now HTTP-only.** The `chroma_path` config field is removed; replace with `chroma_host` (required) and `chroma_port` (default `8000`). `get_collection` no longer accepts an `embedding_model` argument — pass only `name`. Any config YAML or resource instantiation referencing `chroma_path` or `embedding_model` must be updated.

- **`OpenAIEmbedder` moved to `retrievers.embedding`.** The old import path `evals.retrieval.embedder.OpenAIEmbedder` is gone; update to `from retrievers.embedding import OpenAIEmbedder`.

- **`chromadb` (full) replaced by `chromadb-client` across all packages.** `sentence-transformers`, `rank-bm25`, `langchain-experimental`, and `langchain-community` are no longer installed. Any code that called `SentenceTransformerEmbeddingFunction` or BM25/hybrid retrieval helpers must be rewritten.

- **`knowledge-retrievers` is now a base dependency of `knowledge-orchestrators`** (no longer opt-in via `[workbench]`). The `[workbench]` extras group is removed entirely; there is no longer a separate extras gate for RAG dependencies.

- **Production Docker image now includes `retrievers` source.** Previously excluded to avoid ML dep weight; with `chromadb-client` replacing the full `chromadb`+`sentence-transformers` stack, the separation is no longer needed.

### Removed

- **`[workbench]` extras, workbench code location, and all `idx_*` indexing strategies removed.** The `orchestrators.defs.workbench` code location is gone. `poe index`, `poe eval`, and `poe reset-indices` tasks are removed. `dagster-dev` no longer loads the workbench code location. `strategies.yaml`, `op_factories.py`, `StrategyPathsResource`, and all `idx_markdown_*` / `idx_recursive_*` / `idx_semantic_*` Dagster assets are deleted.

- **`eval-retrieval` CLI no longer requires `--extra workbench`.** The eval harness now runs from the base install; the `[workbench]` flag that was previously required to activate the dependency chain is gone.

---

## [0.9.7] — 2026-05-11

### Changed

- **Project framing in `CLAUDE.md` reoriented around the personal-knowledge-OS concept with two compounding exit ramps** — apply (research panel + codebase, owned by `newsletter-assistant`) and retain (wiki + indexing + retrieval, owned by this repo). Adds a "Where we are on the journey" section honest about what works today (backups + wiki synthesis) vs. WIP (index pipeline + evals) vs. aspirational (cross-corpus retrieval ranking, wiki-to-voice-agent bridge), plus a 4-point decision rubric for evaluating future work. Same framing applied in companion repo `newsletter-assistant`.

---

## [0.9.6] — 2026-05-11

### Changed

- **`domains.*` import paths reorganised for source-module symmetry.** `domains.store` is removed; `domains.wiki.sources.{RawStoreSource, LocalFileSource, IngestItem, IngestSource}` are split into their canonical homes. New paths: `RawStoreSource` → `domains.raw_store.sources`; `LocalFileSource` → `domains.notes.sources`; `IngestItem` and `IngestSource` → `domains.types`. Any external code (notebooks, downstream scripts) importing from the old paths will get `ImportError` and must be updated.

---

## [0.9.5] — 2026-05-11

### Added

- **Pinned retrieval eval dataset** (`packages/evals/datasets/retrieval_eval.jsonl`) — 166 labelled query/expected-`content_id` pairs spanning raw_store, sessions, notes, and research corpora. The `eval-retrieval` CLI now defaults `--eval-set` to this file, so `uv run eval-retrieval` works out of the box without specifying a path.

- **`LocalFileSource.get_item_ids` / `get_item`** — `LocalFileSource` in `domains/wiki/sources.py` now implements the full `IngestSource` interface (matching `SessionsSource`, `ResearchSource`, and the raw-store source). All four sources are now interchangeable in eval and ingestion loops.

### Changed

- **Embedding and Chroma upsert no longer hit provider limits on large corpora.** `OpenAIEmbedder` now sub-batches at a 250k-token budget per request (was: unbounded → OpenAI 400 at >300k tokens). Chroma upserts chunk at 4000 items (was: unbounded → server `max_batch_size` error above 5461). Both limits are internal; the CLI and runner API are unchanged.

## [0.9.4] — 2026-05-10

### Added

- **`evals.retrieval` harness + `eval-retrieval` CLI** — scores `(model, dims, chunker)` configs on Recall@5 / MRR@10 / nDCG@10 over expected `content_id`. `OpenAIEmbedder` with tenacity retry on transient errors only; on-disk embedding cache keyed on `(model, dims, sha256(text))`. Results in `data/eval_results/retrieval_<timestamp>.json`.

- **`SessionsSource` and `ResearchSource`** in `domains/{sessions,research}/` — read newsletter-assistant's `sessions.db` and `research.db`. Both assert WAL; `SessionsSource` gates on `ended_at IS NOT NULL`; `ResearchSource` reads `documents.content` directly (the writer commits it with the row).

- **`turn_grouping_chunker`** in `retrievers/chunking/turn_grouping.py`, registered as `"turn_grouping"` — packs consecutive transcript turns into `max_tokens`-bounded windows with overlap, preserving turn boundaries.

- **`packages/domains/README.md`** — layer-purpose doc.

- **`notebooks/` scaffold** + `[notebooks]` workspace extra + `notebooks` poe task — four exploratory notebooks for Phase C work; opt-in JupyterLab via `uv run poe notebooks`. `notebooks/README.md` documents a per-developer (gitignored) `.mcp.json` template for the Claude Code Jupyter MCP server.

### Changed

- **`IngestItem` and `Chunk` hoisted into a shared types module each** — `domains/types.py` for `IngestItem` (with optional `author`/`url`/`started_at`); `retrievers/chunking/types.py` for `Chunk`. Wiki and chunking modules re-export for back-compat.

- **`rag-eval` poe task renamed to `generation-eval`** (named for what it measures, not the library). New `retrieval-eval` task wired to the new harness; `wiki-eval` marked as TODO until that layer lands.

---

## [0.9.3] — 2026-05-09

### Changed

- **`BACKUP_DIR` and `BACKUP_SOURCE_DIR` are now required `dg.EnvVar`** (was: silent fallback to `<repo>/backups` and `~/newsletter-assistant/data`). Misconfigured deploys fail fast at run init. Symmetric with `DATABASE_URL`.

---

## [0.9.2] — 2026-05-09

### Changed

- **`wiki/synthesized` is now bound 1:1 to `backup_readings` snapshots by partition key.** Reads `BACKUP_DIR/<partition_key>/raw_store.db` directly; missing snapshot raises `dg.Failure` immediately — no filesystem scan, no freshness window, no fallback. `WikiResource.backup_dir` replaces `raw_store_db_path`. `SYNTHESIZE_WIKI_DAG_VERSION` 2 → 3.

- **Discovery extracted into a `wiki/pending` asset; schedule fires with no run config.** `wiki/pending` reads `raw_store ∖ wiki.processed` from the partition-bound snapshot, applies the per-tick cap, and emits the work order as `list[str]`; `wiki/synthesized` consumes it via `AssetIn`. `SynthesizeWikiConfig` removed — `Materialize all` from the UI now works without hand-typed run_config. Materialization metadata exposes `total_pending`, `queued`, `capped`, `excluded_by_source`, and `item_ids` for backlog visibility.

- **Source-prefix allowlist gates wiki discovery.** `ALLOWED_CONTENT_ID_PREFIXES = ("medium::",)` in `def_config.py` — only Medium articles enter the wiki today; other raw_store rows are skipped at discovery and counted in `excluded_by_source`. Widen the allowlist once per-source-type prompts land.

- **Per-tick cap is now `WIKI_MAX_PER_TICK` env var** (default 30). Was `MAX_PER_TICK_DEFAULT`, a code-level constant. Previously the executor was a `ThreadPoolExecutor`; synthesis now iterates sequentially — simpler, gentler on rate limits, and sufficient at cap 30 / daily cadence.

### Added

- **LLM cost metadata on every `wiki/synthesized` materialization.** Per-call usage flows through both wiki sub-graphs via a new `llm_calls` reducer; the asset attaches `cost_usd`, `input_tokens`, `output_tokens`, `cost_by_model` (JSON breakdown), and `cost_complete` (false when any item errored). New `workflows/costs.py` pricing table (gpt-4.1-nano, gpt-4.1-mini); unknown models surface in `unknown_pricing_models` rather than failing the run. New `generate_with_usage` / `generate_structured_with_usage` helpers in `workflows.llm`; the structured variant now re-raises `parsing_error` instead of silently returning `None`.

---

## [0.9.1] — 2026-05-08

### Changed

- **`backup_readings` snapshots `research.db` instead of archiving `research_output/`.** Newsletter-assistant's `research.db` already contains the research output, so the tgz was redundant. New asset `snapshots/research` mirrors `raw_store.db`/`sessions.db` (SQLite `.backup()` API + `PRAGMA integrity_check`). Existing `research_output.tgz` files in local backups + Drive are left in place; retention naturally prunes them. `BACKUP_READINGS_DAG_VERSION` 3 → 4.

---

## [0.9.0] — 2026-05-08

### Changed

- **`synthesize_wiki` is now schedule-driven, date-partitioned.** One scheduled tick (cron `0 6 * * *`) = one Dagster run = full pending → synthesized → index cycle. Discovery (`raw_store ∖ wiki.processed`) moved into the schedule; pending item_ids travel as `run_config` rather than per-item dynamic partitions. The asset fans out internally (ThreadPoolExecutor, cap 5) and re-filters against `wiki.processed` so retries don't re-pay for already-committed items.

- **`wiki/index` daily-partitioned with `deps=[wiki/synthesized]`** — the AllPartitionMapping trap is gone now that the partition dimension is bounded.

### Removed

- **`discover_pending_contents` asset and `wiki_items` dynamic partitions** — discovery is no longer an asset; it's a function the schedule calls at fire time. `WIKI_MAX_PER_DISCOVERY` env var dropped (replaced by code-level `MAX_PER_TICK_DEFAULT`).

---

## [0.8.0] — 2026-05-08

### Added

- **`synthesize_wiki` pipeline** — three assets (`discover_pending_contents`, `synthesize_item`, `regenerate_toc`) wrapping a LangGraph workflow that turns raw_store items into wiki pages. Per-partition retry auto-resumes from the LangGraph checkpoint.

- **`WIKI_MAX_PER_DISCOVERY` env var** — caps partitions registered per discovery (default 30). `LANGFUSE_TRACING_ENVIRONMENT` documented in `.env.example` for per-deploy trace tagging.

- **Wiki schema auto-applied on first `compose up`** via `docker/postgres/init/02-apply-wiki-schema.sh`. No manual `psql` step on fresh deploys.

- **Postgres exposed on `127.0.0.1:5432`** — laptop `dagster dev` reaches compose Pg without a port-forward. Loopback-only bind keeps Pg off the public NIC.

- **Upstream source `AssetSpec`s** (`raw_store`, `sessions`, `notes`) in `upstream_sources.py` — anchor lineage in the Dagster UI, connecting `backup_readings` and `synthesize_wiki` via shared upstream nodes.

### Changed

- **`discover_pending_contents` hard-fails above 10 000 raw_store items.** Full-scan discovery isn't right at that scale → migrate to sensor-driven discovery.

- **Partition keys source-prefixed** (`<source>:<id>`, e.g. `raw_store:abc123`) — avoids ID collisions across sources (notes, sessions, raw_store).

- **`DATABASE_URL` required** — `WikiResource` uses `dg.EnvVar`; unset → fails at run init. `get_checkpointer(db_url: str)` no longer accepts `None` or env fallback.

- **Discovery queries IDs only** via new `get_content_ids()` in `domains/store.py` — full content markdown no longer loaded for the diff.

---

## [0.7.0] — 2026-05-07

### Added

- **`notes/` and `research_output/` are now included in daily backups.** The `backup_readings` pipeline snapshots both directories as gzip-tar archives (`notes.tgz`, `research_output.tgz`), verifies each with a blocking integrity check (readable archive, at least one member), and includes them in the Drive upload and prune cycle. Missing source dir → hard `Failure`. Implementation: `snapshot_notes` / `snapshot_research_output` assets + `verify_snapshot_notes` / `verify_snapshot_research_output` checks in `backup_readings/`; `ARCHIVE_DIRS` in `config.py`; `BackupResource.expected_files` property. `BACKUP_READINGS_DAG_VERSION` 2 → 3.

---

## [0.6.7] — 2026-05-07

### Added

- **Co-emitted blocking checks on `storage_capacity` and `uploaded_snapshots`.** `drive_capacity_below_threshold` enforces the >90% Drive cap; the asset now always materializes so `used_pct` timeseries survives threshold violations. `all_snapshots_uploaded` re-lists Drive post-copy and catches rclone silent drops.
- **`DRIVE_BACKUP_ROOT` env var** (required) — path prefix under `DRIVE_REMOTE`. Lets dev deploys point at a sibling dir (`...-dev`) without overwriting prod. Unset → fails fast at run init.
- **`packages/orchestrators/STYLEGUIDE.md`** — checked-in conventions for Dagster pipelines in this repo.

### Changed

- **`check_drive_capacity` renamed to `storage_capacity`** (measurement-only; threshold moved to its blocking check). `BACKUP_READINGS_DAG_VERSION` 1 → 2.
- **`compute_kind` uses icon-supported names**: `rclone`/`google_drive` → `googledrive`; `filesystem` → `file`.
- **`backup_readings/README.md` reframed as a runbook** — failure-cascade diagram + ops + external setup; dropped code-mirroring tables.
- **Redundant `status: "ok"` metadata removed** from 5 assets.
- **`ping_healthcheck_on_success` sensor evaluated hourly** (`SENSOR_MIN_INTERVAL_S` 300 → 3600). Daily job — 60 min eval cadence is invisible against healthchecks' day+hour period+grace window.

## [0.6.6] — 2026-05-07

### Changed

- **Webserver binds two ports now**: `127.0.0.1:3030` always (SSH tunnel) plus `${APP_HOST:-127.0.0.2}:3030` for sibling-container reach. Default is a no-op loopback alias so `docker compose up` works without env. Previously, setting `APP_HOST=172.17.0.1` for Caddy silently broke the tunnel.

## [0.6.5] — 2026-05-07

### Changed

- **`DRIVE_REMOTE` and `HEALTHCHECK_PING_URL` are now required for `backup_readings`.** Previously unset → silent skip of Drive upload + healthcheck ping. Now → run fails fast at startup. Implementation: `RcloneResource` / `HealthcheckResource` use `dg.EnvVar()`; `is_configured` short-circuits removed.
- **Dagster webserver mounts at `/dags`** for reverse-proxy use. Docker UI is now at `http://localhost:3030/dags`; `poe dev` (local) is unchanged at `:3030`. Implementation: `--path-prefix=/dags` on the webserver entrypoint in `docker-compose.yml`; healthcheck probes `/dags/server_info`.
- **Deploy a feature branch from CLI** via `./scripts/deploy-hcloud.sh deploy --branch <name>` (default still `main`). Forwards through poe with the `--` separator: `uv run poe deploy -- --branch fix/foo --no-build`. Warn banner when the branch isn't `main`.
- **`APP_HOST` documented in `.env.example`** for operators fronting Dagster with a reverse proxy. Default `127.0.0.1` (laptop); set `172.17.0.1` on a server where a sibling Caddy container reaches Dagster via the docker0 gateway.

## [0.6.4] — 2026-05-07

Fix failed snapshot tasks in DAG "backup_readings" not raising error. Update `.env.example` to use simpler format.

### Added

## [0.6.3] — 2026-05-07

### Added

- **`APP_UID` / `APP_GID` in `.env`** — set them to the host deploy user's `id -u`/`id -g` so the container's `dagster` user shares the bind-mount owner's uid. No more chown ceremony for `./data`, `./logs`, `./backups`, `./.rclone`. Default 1001 keeps backward compatibility.
- **Per-step compute logs at `./logs`.** `LocalComputeLogManager` now writes to a bind-mounted `/app/logs` instead of the image default `/opt/dagster/storage`, which the non-root container user couldn't write.
- **Healthchecks for `dagster-webserver` (`/server_info` via stdlib urllib) and `dagster-daemon` (`liveness-check`).** Compose's `depends_on: service_healthy` now meaningfully waits on all three Dagster services.

### Changed

- **Production Docker image: ~17 GB → ~3 GB.** `knowledge-retrievers` was an unconditional dep of `workflows` even though only `workflows.agents` uses it; this transitively pulled in PyTorch + CUDA + chromadb. Moved to a `workflows[agents]` extra (activated via `orchestrators[workbench]`), so the prod image now installs only what wiki + backup pipelines need.
- **Container runs non-root with a real `$HOME=/home/dagster`.** Deliberate uid/gid (default 1001), no skel pollution. `langchain` added as an explicit `workflows` dep — `langfuse.langchain.CallbackHandler` needs the umbrella package and was previously pulled in only via workbench tooling.
- **`BACKUP_SOURCE_DIR` is now a host-path env consumed by compose.** Compose bind-mounts it to `/app/source` and overrides the in-container env var to that fixed path. Set in `.env` using `${HOME}/...` — compose doesn't tilde-expand.
- **Reproducible rebuilds.** Dagster framework image pins `dagster*` via build ARGs to match `uv.lock`; rclone pinned to v1.74.0 via precompiled binary (replaces `curl | bash`); both `uv sync` layers use BuildKit cache mounts so a one-package bump no longer re-downloads every wheel.
- **Faster startup.** `dagster-code` invokes `/app/.venv/bin/dagster` directly; the previous `uv run` form was re-syncing the workspace on every container start, pulling 5 workbench packages in ~13 s.
- **rclone config moves to `/home/dagster/.config/rclone` (rclone's default `$HOME` lookup).** No `RCLONE_CONFIG` env override needed.
- **Postgres image bumped 14 → 16.** Existing deploys: `pg_dumpall` against PG14 → recreate the volume → restore on PG16 before rebuilding.
- **`scripts/deploy-hcloud.sh` reads `DEPLOY_PASSWORD` from `.env.deploy`** and pipes it into remote `sudo -S`, so non-TTY SSH sudo works without a NOPASSWD sudoers entry.

### Removed

- **`RCLONE_CONFIG` env override** — redundant now that the config is mounted at the standard `$HOME/.config/rclone/` path.

---

## [0.6.2] — 2026-05-06

### Changed

- **Production Docker image drastically smaller** — `knowledge-retrievers` and `knowledge-evals` are now optional extras (`[workbench]`) in `knowledge-orchestrators`, so the production image no longer installs sentence-transformers, PyTorch, chromadb, or ragas (~8 GB of ML deps only needed for the RAG workbench). The image CMD now loads `orchestrators.defs.pipelines.definitions` (backup + wiki) instead of the full merger. Local dev is unchanged: `uv sync` at the workspace root still installs everything.
- **uv cache permission crash on deploy fixed** — the `dagster` container user is created without a home directory, causing uv to fail creating `~/.cache/uv` at startup. `ENV UV_NO_CACHE=1` is now set in the Dockerfile so no home directory is needed.

---

## [0.6.1] — 2026-05-06

### Changed

- **`dagster-code` now loads `.env` via `env_file`** — eliminates manual `DAGSTER_POSTGRES_*` duplication in docker-compose. `dagster-webserver` and `dagster-daemon` retain only their built-in env vars (least-privilege). Implementation: `docker-compose.yml` `dagster-code` service.
- **Dev tunnel port corrected to 3030** — was 3000, which mismatched the `poe dev` / `poe tunnel` host port. `.env.example` reorganised by consumer group for clarity.

---

## [0.6.0] — 2026-05-06

Revamp DAG "backup_databases" to "backup_readings". The tasks are configured following best practices from "dagster-open-plaform" project. DAG will backup sqlite databases in local and on cloud at Google Drive.


## [0.5.0] — 2026-05-06

Phase B — LangGraph wiki synthesis migration. The wiki synthesis pipeline is now a checkpointed LangGraph workflow with Send-API per-entity fan-out, transactional Postgres commits, and dynamic-partitioned Dagster materialization. Manual real-data validation deferred to a follow-up PR.

- **`workflows/shared/`** — new home for cross-workflow plumbing. `checkpointer.get_checkpointer(db_url=None)` is a context manager that yields a `langgraph.checkpoint.postgres.PostgresSaver` bound to a fresh psycopg connection; falls back to `DATABASE_URL` env var; calls `setup()` on entry. `observability.get_langfuse_callback()` returns a process-cached `langfuse.langchain.CallbackHandler` when `LANGFUSE_PUBLIC_KEY` is set, otherwise `None` (no warning).
- **`workflows/llm.py`** — both `generate` and `generate_structured` now pass `config={"callbacks": [...]}` to LangChain when the Langfuse callback is configured. No-op when env unset; existing behavior unchanged.
- **`domains/wiki/state.py`** — Postgres helpers backing the new workflow's terminal commit. Pure functions taking a `psycopg.Connection`; callers manage transactions. `insert_processed`, `get_processed_ids`, `get_failed`, `upsert_page`, `get_page`, `get_all_pages`, `insert_aliases_idempotent` (uses `ON CONFLICT (alias) DO NOTHING` for concurrent-partition safety), `snapshot_aliases` (reads aliases into the existing in-memory `AliasStore`).
- **`pytest-postgresql>=6.0,<8.0`** added to root dev deps. `tests/conftest.py` exposes a `wiki_pg` fixture that yields a fresh psycopg connection to a temp Postgres with `wiki.sql` loaded — used by `tests/wiki/test_state_pg.py` (11 new tests covering all helpers plus PK/upsert/concurrency edges).
- **`workflows/wiki_synthesis/`** — the new LangGraph workflow that replaces `workflows/wiki/ingest.py` (the legacy folder is now removed; see "PR 3 — rewire and cleanup" below). Files:
  - `graph.py` — parent `StateGraph` (one document per invocation). `extract_entities` → conditional fan-out via `langgraph.types.Send` → per-entity sub-graph → `commit`. The `WikiSynthesisState` TypedDict declares an `Annotated[list[dict], operator.add]` reducer on `entity_results` so concurrent sub-graphs concatenate their results into the parent state without collision.
  - `entity_graph.py` — per-entity sub-graph with one node (`process_entity`) and a restricted `EntityWorkflowOutput` schema so only `entity_results` flows back to the parent (avoids the `InvalidUpdateError` that LangGraph 1.x raises when multiple Sends try to write the parent's input keys).
  - `nodes.py` — `extract_entities` snapshots aliases from Postgres, calls the extraction LLM, stages new aliases for commit. `commit` opens one Postgres transaction and writes `wiki.pages` rows for each successful entity, `wiki.aliases` for staged tuples (`ON CONFLICT DO NOTHING`), and the single `wiki.processed` row — atomic per the plan's "same transaction" rule. Status mapping covers ok / error / skipped / partial-success.
  - `parsing.py` — pure helpers (`parse_llm_page_output`, `check_h2_preservation`, `slug_from_id`) lifted out of legacy `ingest.py` so the new workflow doesn't depend on the soon-to-be-deleted module.
- **`workflows/wiki_synthesis/` invocation pattern** — caller compiles the graph with a checkpointer from `workflows.shared.checkpointer.get_checkpointer()` and invokes with `thread_id=f"wiki_synthesis__{item_id}"`. Replay after crash resumes from per-entity sub-graph checkpoints; only failed entities re-run the synthesis LLM call.
- **`wiki.processed.status` CHECK constraint** added to `wiki.sql` — values must be `'ok'`, `'error'`, or `'skipped'`. Prevents silent drift if a future caller writes a different string.
- **Extraction failures now write a status='error' processed row** (new behavior — legacy raised and left no DB footprint, causing infinite Dagster retries on a permanent extraction failure).
- **Parity tests** ported from `tests/wiki/test_ingest.py` to `tests/wiki_synthesis/{test_parsing,test_stage_aliases,test_graph}.py`. 16 new tests; LLM calls mocked at import locations, Postgres assertions run against the real `wiki_pg` fixture so the transactional commit path is genuinely exercised.
- **`workflows/wiki_synthesis/runner.py::invoke_wiki_synthesis`** — the canonical invocation pattern. Bundles PostgresSaver checkpointer, `thread_id="wiki_synthesis__{item_id}"`, the Langfuse callback at `graph.invoke` level (not just per-LLM-call), and `langfuse_session_id` + tags metadata so retries/replays of the same item group into one Langfuse session view. Callers (Dagster asset in PR 3, future CLI, ad-hoc scripts) should prefer this over compiling the graph manually. Three new tests cover end-to-end invocation, callback propagation when `LANGFUSE_PUBLIC_KEY` is set, and silent omission when unset.
- **PR 2 new-capability test suite** under `tests/wiki_synthesis/`. Verifies the properties that justify using LangGraph at all:
  - **Replay correctness** (`test_replay.py`) — when `commit` raises mid-txn, the post-fan-out checkpoint is preserved; resuming via `graph.invoke(None, config)` re-runs only `commit`. The synthesis LLM is **not** re-called (the architecturally important property — no LLM cost on commit-time DB failure). Note: the original "per-entity replay" claim was overstated; Send fan-out is one atomic super-step from the checkpointer's perspective. Documented in the test docstring.
  - **Send-API parallelism** (`test_parallelism.py`) — N synthesis calls routed through `threading.Barrier(N, timeout)`; barrier only releases when all N threads arrive. If Send were serialized, only one thread would arrive and the test fails fast.
  - **Commit txn atomicity** (`test_commit_atomicity.py`) — patching `insert_processed` or `upsert_page` to raise mid-txn aborts the whole transaction; `wiki.pages`, `wiki.aliases`, `wiki.processed` all stay empty. The `.md` files DO exist on disk (`write_page` is file-atomic, outside the txn boundary) — pinned in the test as intentional.
  - **Concurrent alias safety** (`test_alias_concurrency.py`) — 10 threads in two groups of 5 race for the same two aliases via separate psycopg connections; `ON CONFLICT (alias) DO NOTHING` lands exactly one row per alias, no deadlock, no exception.
- **`runner.py` auto-resume** — `invoke_wiki_synthesis` now checks `graph.get_state(config).next`. If the thread is paused mid-execution (prior invocation raised before END), it invokes with `None` to resume. Otherwise (non-existent thread or successfully ended thread) it invokes with the input state for a fresh run. Critical because `invoke(state, config)` on an existing thread restarts from START — silently re-firing every entity LLM call. The completed-thread re-run case is intentional (Dagster re-materializing should re-run with current inputs) and pinned by `test_runner_re_runs_on_completed_thread`.
- **`pytest-timeout>=2.3,<3.0`** added to dev deps so threading-based tests use `@pytest.mark.timeout(N)` and never hang the suite.
- **Shared test helpers** at `tests/wiki_synthesis/_helpers.py` (`make_item`, `make_extraction`, `build_synthesis_output`, `extract_entity_id_from_prompt`) — extracted from the parity tests so the new-capability tests don't duplicate factories.

#### PR 3 — rewire and cleanup

- **`wiki_synthesized` Dagster asset** is now dynamic-partitioned (`DynamicPartitionsDefinition(name="wiki_items")`) and invokes `invoke_wiki_synthesis(item, db_url, wiki_dir)` per partition. Per-partition retries auto-resume from the LangGraph checkpoint via the runner's auto-resume heuristic — no LLM re-calls if the prior failure was in commit.
- **`wiki_pending`** becomes a discovery asset: it scans raw_store for items not in `wiki.processed` (status=ok or skipped) and registers them as new `wiki_items` partitions on the Dagster instance. Idempotent re-runs only add what isn't already a partition.
- **`wiki_index_updated`** reads from `wiki.pages` (Postgres) via `domains.wiki.state.get_all_pages`. Intentionally NOT declared as `deps=[wiki_synthesized]` because the default `AllPartitionMapping` would block the index on every partition completing — never true with dynamic partitions. Phase E will add a sensor; for now, materialize manually.
- **`WikiResource`** gains a `database_url` field that resolves at call time via `get_database_url()` (Dagster idiom — pass `dg.EnvVar("DATABASE_URL")` at construction or rely on env-var fallback). Removed `state_db_path` and `aliases_path` (legacy SQLite/YAML).
- **`domains.store.get_content_by_id(content_id, *, db_path)`** — focused single-row lookup the partitioned asset needs (vs. loading every row via `get_contents`).
- **`domains.wiki.sources.RawStoreSource.get_item(item_id)`** — convenience wrapper on top of `get_content_by_id`.
- **Deleted**:
  - `packages/workflows/src/workflows/wiki/{__init__,ingest,state}.py` and the now-empty `wiki/` folder.
  - `tests/wiki/test_ingest.py`, `tests/wiki/test_state.py` — the parity tests for the deleted code, now redundant with `tests/wiki_synthesis/`.
- **Moved**: `workflows/wiki/prompts.py` → `workflows/wiki_synthesis/prompts.py` (still in use by `entity_graph.py` and `nodes.py`; imports rewired).

---

## [0.4.0] — 2026-05-01

Phase A foundation release — restructure into a uv workspace with Postgres infrastructure ready for the LangGraph wiki workflow (Phase B). No behavior change; 90/90 tests pass.

- **uv workspace with 5 packages** under `packages/{domains,workflows,retrievers,evals,orchestrators}/`. Cross-package import discipline enforced via `pyproject.toml` deps:
  - `knowledge-domains` — pure data layer (pydantic, psycopg, pyyaml, python-frontmatter); no LLM/ML/Dagster deps.
  - `knowledge-workflows` — depends on domains + retrievers; adds langgraph, langgraph-checkpoint-postgres, langchain-openai, langchain-anthropic, langfuse.
  - `knowledge-retrievers` — depends on domains; adds chromadb, sentence-transformers, rank-bm25, langchain-text-splitters, langchain-experimental, langchain-community, tiktoken.
  - `knowledge-evals` — depends on domains + workflows + retrievers; adds ragas.
  - `knowledge-orchestrators` — depends on all four others; the only package allowed to depend on Dagster, dagster-postgres, dagster-webserver, dagster-dg-cli, poethepoet.
- **Code migrated into the 5 packages** — the old `src/knowledge_pipeline/` tree split across the new packages and deleted:
  - `domains/`: `store.py`, `wiki/{types,io,aliases,sources}.py`, `wiki/schema/wiki.sql`
  - `retrievers/`: `chunking/`, `postprocess/`, `retrieval/` (cosine, hybrid, rerank, fusion, registry), `vector_store/chroma.py`
  - `workflows/`: `llm.py`, `wiki/{prompts,state,ingest}.py`, `agents/nodes/query_rewrite.py` (was `lib/retrieval/hyde.py`)
  - `evals/`: `rag.py` (was `lib/eval.py`)
  - `orchestrators/`: `definitions.py`, `config.py`, `strategies.{py,yaml}` (the `.py` was `lib/utils.py`), and the entire `defs/` tree (`shared/`, `workbench/`, `pipelines/`).
- **Boundary fixes** — `domains.store` and `retrievers.vector_store.chroma` no longer import `orchestrators.config`. Path arguments (`db_path`, `chroma_path`) are now passed in by the caller. `db_path` is keyword-only across all `domains.store` functions. `HyDE` retrieval has an LLM dep so it lives in `workflows.agents.nodes.query_rewrite`, not in `retrievers`.
- **Workspace package pip-names prefixed with `knowledge-`** — matches the `newsletter-assistant` workspace pattern: prefix lives only in `pyproject.toml` `dependencies` and `[tool.uv.sources]` keys; import paths stay plain (`from domains import …`). Pre-empts cross-project name collisions when `newsletter-assistant` consumes `knowledge-workflows`.
- **Root project shape** — `pyproject.toml` is now a virtual project: `[project].dependencies` lists all 5 prefixed members, no `[build-system]`, no `[tool.hatch.*]`. Bare `uv sync` (without `--all-packages`) installs the full workspace.
- **Postgres `knowledge_pipeline` database** — idempotent `docker/postgres/init/01-create-knowledge-db.sh` mounted into the existing Dagster postgres service via `/docker-entrypoint-initdb.d` (single instance, no second container).
- **`packages/domains/src/domains/wiki/schema/wiki.sql`** — Postgres schema for wiki state: `wiki.processed` (PK `(item_id, source_type)`), `wiki.pages` (PK `entity_id`, jsonb columns for `related`/`sources`/`source_types`), `wiki.aliases` (UNIQUE `alias`, indexed by `entity_id`). Ready for Phase B.
- **Dagster code locations preserved as workbench / pipelines** — `poe dev` loads both `orchestrators.defs.workbench.definitions` and `orchestrators.defs.pipelines.definitions` as separate code locations (matches the original split). `orchestrators.definitions` is the merger used by Docker/prod (single `-m` flag in `configs/workspace.yaml` + `docker/code/Dockerfile`). `[tool.dagster].module_name` points at the merger; `code_location_name` is `orchestrators`.
- **CLI migrated from `dagster job execute` to `dg launch`** — Dagster 1.13 deprecated the former. `poe index/backup/eval` now use `dg launch -m … --job …`. Added `dagster-dg-cli` to the orchestrators deps.
- **Dev port moved from 3000 to 3030** — `poe dev`, `poe tunnel` (host side), `docker-compose` host port mapping, README references. Container internal port and remote production port stay at 3000.
- **Docker code-server image** rebuilt for the workspace layout — copy each member's `pyproject.toml` first for layer-cached deps resolution, then per-member `src/` for editable installs. Switched from `pip install uv` to a binary copy from `ghcr.io/astral-sh/uv:latest` (~150s faster). Use `--no-install-workspace --package knowledge-orchestrators` so only the deployable's dep tree is resolved.
- **New poe tasks** (stubs for Phase B) — `wiki-ingest`, `wiki-lint`, `research`, `wiki-eval`, `rag-eval`, `reset-wiki`, `reset-checkpoints`, `reset-everything`.
- **Removed** `src/knowledge_pipeline/` — the entire old source tree.

## [0.3.0] — 2026-04-28

### Added

- **Wiki synthesis pipeline** — LLM-powered knowledge distillation that reads raw articles and produces wiki pages
  - Entity extraction (gpt-4.1-nano) identifies concepts, tools, and trends
  - Page synthesis (gpt-4.1-mini) creates or updates wiki pages per entity
  - Asset-based Dagster architecture (`wiki_synthesized`, `wiki_pending`, `wiki_index_updated`)
- **`lib/wiki/`** — core library with no Dagster dependencies:
  - `types.py` — Pydantic models (WikiPage, ExtractedEntity, ExtractionResult)
  - `io.py` — markdown + YAML frontmatter read/write with atomic writes
  - `aliases.py` — entity alias resolution with fuzzy matching (difflib, 0.85 threshold)
  - `state.py` — SQLite state tracking (WAL mode, transactional updates)
  - `sources.py` — source adapters (RawStoreSource, LocalFileSource)
  - `ingest.py` — orchestration: extract → synthesize → write → update state
  - `prompts.py` — LLM system/user prompts
- **Robustness** — atomic file writes (`os.replace`), transactional state DB, LLM output validation, staged alias persistence

---

## [0.2.0] — 2026-04-20

### Added

- **LLM client** — `lib/llm.py` with LangChain wrapper (`generate`, `generate_structured`) for provider-agnostic LLM calls with Pydantic-validated structured output
- **Wiki config** — `wiki` section in `strategies.yaml` (synthesis model, page types, collection name, embedding model)
- **`langchain-openai`** dependency (replaces direct `openai` SDK)
- **CHANGELOG.md** — initial changelog covering project history

---

## [0.1.0] — 2026-04-20

Initial baseline. Dagster-based RAG strategy workbench with evaluation harness.

### Added

- **Index strategies** — four pluggable chunking + embedding combinations:
  - `idx_markdown_minilm` — markdown-aware chunking + MiniLM (baseline)
  - `idx_markdown_bge` — markdown chunking + BGE-small-en-v1.5
  - `idx_recursive_minilm` — recursive character splitting + MiniLM
  - `idx_semantic_minilm` — semantic chunking (embedding similarity splits) + MiniLM
- **Retrieval strategies** — four retrieval methods:
  - `cosine` — basic vector similarity
  - `rerank` — two-stage with cross-encoder reranking
  - `hybrid` — BM25 + vector + Reciprocal Rank Fusion
  - `rerank_hybrid` — hybrid candidates reranked by cross-encoder
- **Evaluation harness** — ops-based job comparing all (collection x retrieval) combos with recall@k, precision@k, MRR metrics across 40 curated queries
- **Chunking registry** — pluggable chunking strategies via `lib/chunking/registry.py`
- **Op factories** — `create_chunk_batch_op`, `create_embed_batch_op`, `create_index_op` for strategy-specific Dagster ops
- **Static dataset** — pinned `raw_store.db` snapshot for reproducible evaluation
- **Database backup job** — scheduled backup of SQLite and ChromaDB data
- **Docker deployment** — Dockerfiles and docker-compose with separate code location server
- **SSH tunnel task** — `uv run poe tunnel dagster` for remote UI access
- **Code locations** — split into `workbench/` (index + eval) and `pipelines/` (backup)
