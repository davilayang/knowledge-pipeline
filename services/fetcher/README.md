# Fetcher service

URL -> markdown, plus LLM extraction over an already-fetched body, for the Knowledge OS project.
Single source of truth for content fetching and for extraction across the KOS services — called
by both this repo's Dagster pipeline and the sibling `newsletter-assistant` repo.

---

## Critical: single-worker invariant is load-bearing

This service runs with **`uvicorn ... --workers 1`** as the only correct invocation.

The in-memory per-key `asyncio.Semaphore` instances are correctness primitives, not
optimization knobs. Multi-worker uvicorn splits them per process:

- arxiv ban risk doubles immediately
- paid-tier dedup breaks, causing duplicate LlamaParse / Groq Whisper calls
- the per-URL `asyncio.Lock` stops being a single-flight primitive

If performance becomes slow, profile first. If the service needs horizontal scale, replace the
in-memory primitives with redis-backed equivalents before raising worker count.

Canonical Dockerfile `CMD` line:

```dockerfile
CMD ["uvicorn", "fetcher.app:app", "--workers", "1", "--host", "0.0.0.0", "--port", "8000"]
```

---

## What's inside

- **`POST /v1/fetch`** — sync single-URL fetch; returns markdown + provenance with ETag / `If-None-Match` → 304 support.
- **`POST /v1/fetches`** — async batch; per-item job_id, `GET /v1/fetches/{job_id}` for status, `DELETE` for real in-process cancellation.
- **`POST /v1/structure`** — content-keyed counterpart to `/v1/fetch` for user-pasted bodies. Two-stage cascade (trafilatura → OpenAI/Ollama Cloud chain) returns the same `FetchResult` wire shape; cascade exhaustion surfaces as `application/problem+json` (502 transient, 503 unconfigured). Cloud chain config: `config/structurer.yaml`; prompt `prompts/structure_v2.md`, selected server-side by `FETCHER_STRUCTURER_PROMPT_PATH` (a service change, not a client header) — superseded versions stay on disk as eval baselines. Response metadata carries no prompt identifier, so a stored row doesn't record which prompt produced it.
- **Structurer fidelity eval** (`evals/`) — scores how much of a pasted body survives `/v1/structure` and A/Bs two prompts. Dev-only; not in the image.
- **`GET /v1/canonicalize`** — exposes URL normalization with cached results in `url_aliases`.
- **`POST /v1/extract`** — structured LLM extraction over an already-fetched body. Takes `content` + `content_type` plus a list of tasks from the closed set `metadata`, `narrative`, `topic_card`, `followups`; returns each task's typed payload. Tasks always run in one fixed, service-chosen order (`metadata` first) because they share a single OpenAI prompt-cache prefix over the article — a caller cannot reorder them. A task that fails does not fail the whole request: the response is still 200, carrying whatever tasks succeeded plus a per-task entry in `errors[]`. Model: `config/extraction.yaml` (single backend, no fallback chain — unlike `config/structurer.yaml` / `config/whisper.yaml`), env override `FETCHER_EXTRACTION_CONFIG_PATH`. Prompts: `prompts/extraction/` under `extraction_prompts_root` (env `FETCHER_EXTRACTION_PROMPTS_ROOT`, default `prompts`); the image copies the repo-root `prompts/extraction/` into `/app/prompts/extraction/`. Route: `src/fetcher/endpoints/extract.py`; task/model/prompt/cache logic: `src/fetcher/extract/`.
- **`GET /v1/extract/prompts`** — reports the configured model and each task's active prompt label + staleness sha, without running anything, so a caller can decide whether a stored extraction is still current before paying for a new one.
- **Handlers:**
  - `arxiv` — pymupdf (50MB cap) → LlamaParse agentic_plus (strict paid).
  - `youtube` — transcript-api with oEmbed metadata header.
  - `medium` — Jina → mediumapi.com RapidAPI paywall bypass (paid). Host set: shared `domains.medium_urls` (`medium.com` + `*.medium.com` + known publications).
  - `facebook` — RapidAPI (api4 → scraper3), no free tier.
  - `github` — raw `README.md` from `raw.githubusercontent.com/<org>/<repo>/HEAD/README.md` (repo-root URLs only; no README → error-state).
  - `file_pdf` — pymupdf4llm (50MB cap) → LlamaParse agentic_plus (paid). Generic-PDF URLs that don't match arxiv.
  - `file_audio` — Whisper transcription for audio/video-file URLs (mp3/m4a/mp4/… — suffix set shared via `domains.AUDIO_SUFFIXES`).
  - `article` — Jina → curl_cffi+trafilatura → Tavily Extract (paid). The catch-all.
- **Preference-ordered tier cascade** per handler: walk tiers in each handler's declared order (free-first for most; a quality-first handler like `arxiv` may list its paid tier first), stopping at the first to clear the quality floor. Paid tiers are gated on `allow_paid=true` wherever they sit in the order.
- **SQLite store** with four tables: `fetch_cache`, `extraction_cache`, `async_jobs`, `url_aliases` — owned by `domains.fetches_store`.
- **Container** in this repo's docker-compose stack, attached to `dagster_network` and `kos-network` with the alias `kp-fetcher`.

## Structurer fidelity eval

`/v1/structure`'s one hard requirement is stripping boilerplate without
rewriting the article; it fails by quietly summarising instead. The harness
that measures this, and what its score does and doesn't cover, is documented in
[`evals/README.md`](evals/README.md).

## Live API reference

The running service serves auto-generated docs at:

- **Swagger UI**: `/docs` (interactive, supports "Try it out")
- **ReDoc**: `/redoc` (read-only, prints cleaner)
- **Raw OpenAPI**: `/openapi.json`

Local: `http://localhost:8001/docs`. Container on hcloud: `https://<tailnet-host>/fetcher/docs`. The schema is the authoritative endpoint list — README sections below describe behaviour, but `/docs` is what you click through to learn the wire shape.

## Network Setup

The `kos-network` Docker network is shared by KOS compose stacks. It must exist before either
stack starts:

```bash
docker network create kos-network 2>/dev/null || true
```

The deploy script (`scripts/deploy-hcloud.sh setup`) runs this automatically.

## Required Envs

See the fetcher section in `.env.example` at the repo root for the full list with
required-vs-optional annotations. `FETCHER_JINA_API_KEY` is optional — the free tier works
without auth at lower rate limits. The service's `/healthz` returns
`503 {"ok": false, "missing": [...]}` listing required envs that are unset.

## Reachability

- From this repo's containers: `http://fetcher:8000`
- From other KOS containers on `kos-network`: `http://kp-fetcher:8000`
- From the host: not exposed on a host port by default
