# Fetcher service

URL -> markdown for the Knowledge OS project. Single source of truth for content fetching across
the KOS services.

---

## Critical: single-worker invariant is load-bearing

This service runs with **`uvicorn ... --workers 1`** as the only correct invocation.

The in-memory per-source `asyncio.Semaphore` instances planned for Phase 1 are correctness
primitives, not optimization knobs. Multi-worker uvicorn splits them per process:

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

## Phase 0

- FastAPI app with `/healthz`
- SQLite schema with three empty tables: `cache`, `fetches`, `url_aliases`
- Container in this repo's docker-compose stack, attached to `dagster_network` and `kos-network`
  with the alias `kp-fetcher`
- No source-fetching logic yet; that lands in Phase 1

## Network Setup

The `kos-network` Docker network is shared by KOS compose stacks. It must exist before either
stack starts:

```bash
docker network create kos-network 2>/dev/null || true
```

The deploy script (`scripts/deploy-hcloud.sh setup`) runs this automatically.

## Required Envs

See the fetcher section in `.env.example` at the repo root. The service's `/healthz` returns
`503 {"ok": false, "missing": [...]}` listing required envs that are unset.

## Reachability

- From this repo's containers: `http://fetcher:8000`
- From other KOS containers on `kos-network`: `http://kp-fetcher:8000`
- From the host: not exposed on a host port by default

## Design Plan Reference

Full design in `ai-plannings/2026-06-06_fetcher-service.md`.
