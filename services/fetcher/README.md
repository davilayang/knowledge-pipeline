# Fetcher service

URL -> markdown for the Knowledge OS project. Single source of truth for content fetching across
the KOS services.

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
- **`POST /v1/structure`** — content-keyed counterpart to `/v1/fetch` for user-pasted bodies. Three-stage cascade (trafilatura → conservative markdown passthrough → OpenAI/Ollama Cloud chain) returns the same `FetchResult` wire shape; cascade exhaustion surfaces as `application/problem+json` (502 transient, 503 unconfigured). Cloud chain config: `config/structurer.yaml`. Prompt: `prompts/structure_v1.md` (server-side `_PROMPT_VERSION` — bumping is a service change, not a client header).
- **`GET /v1/canonicalize`** — exposes URL normalization with cached results in `url_aliases`.
- **Handlers:**
  - `article` — Jina → curl_cffi+trafilatura → Tavily Extract (paid).
  - `arxiv` — pymupdf → LlamaParse agentic_plus (strict paid).
  - `youtube` — transcript-api with oEmbed metadata header.
  - `medium` — Jina → mediumapi.com RapidAPI paywall bypass (paid). Domain set loaded from `src/fetcher/data/medium_domains.yaml` (ships inside the package via hatchling `force-include`; `_load_domains` fails fast on missing/empty file).
  - `pdf` — pymupdf4llm (50MB cap) → LlamaParse agentic_plus (paid). Routes generic-PDF URLs that don't match arxiv.
- **Free-first tier cascade** per handler: walk free tiers, escalate to paid only when `allow_paid=true` and the quality floor isn't met.
- **SQLite cache** with three tables: `cache`, `fetches`, `url_aliases` — owned by `domains.fetches_store`.
- **Container** in this repo's docker-compose stack, attached to `dagster_network` and `kos-network` with the alias `kp-fetcher`.

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
