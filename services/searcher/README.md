# searcher

`POST /v1/search` for newsletter-assistant's voice `recall` and MCP
`search_knowledge`. The response shape, status rules and versioning are the
hub contract `documents/knowledge-os/contracts/search.md` in
`davilayang/data-context-builder`; change the contract before this service.

## State

Request validation and the response models are in place. No lane is wired, so
every request is answered `unavailable` with each requested lane `skipped`, and
no caller uses the service yet.

## Run

```bash
uv run poe test-searcher                      # its suite (a separate pytest run)
uv run uvicorn searcher.app:app --port 8001   # locally
```

In compose it is the `searcher` service, reachable on the shared
`kos_network` as `kp-searcher:8000`.
