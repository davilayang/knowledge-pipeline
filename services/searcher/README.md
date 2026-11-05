# searcher

`POST /v1/search` for newsletter-assistant's voice `recall` and MCP
`search_knowledge`. The response shape, status rules and versioning are the
hub contract `documents/knowledge-os/contracts/search.md` in
`davilayang/data-context-builder`; change the contract before this service.

## State

Every request runs through the engine (`src/searcher/engine.py`): the requested
lanes run concurrently on one shared query embedding, each reports `ok`,
`failed`, `timed_out` or `skipped`, pieces found by several lanes merge into one
item, items are ordered by best lane rank and cut to `k`, and the status follows
the contract's precedence.

- **Deadlines:** a voice search answers within 5 s, embedding included, and
  lanes still running then are cancelled and reported `timed_out`. An MCP search
  has no deadline. Each lane is given the deadline so its clients can time out
  inside it.
- **Trace:** `trace.stages_ms` holds each stage's own duration (`embed`,
  `lanes`, `merge`) and the `total`; `code_rev` comes from the `CODE_REV`
  environment variable.

No lane is wired yet, so every request is answered `unavailable` with each
requested lane `skipped`, and no caller uses the service yet.

## Run

```bash
uv run poe test-searcher                      # its suite (a separate pytest run)
uv run uvicorn searcher.app:app --port 8001   # locally
```

In compose it is the `searcher` service, reachable on the shared
`kos_network` as `kp-searcher:8000`.
