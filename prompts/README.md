# `prompts/` — Versioned prompt assets

Markdown prompt bodies consumed by the workflow layer. Treated as **content artefacts**, not code — versioned in git, reviewable in PRs, decoupled from package boundaries.

## Layout

````
prompts/
  eval/           # consumed by evals.wiki (FaithfulnessJudge, SpecificityJudge, TaggingJudge)
  extraction/     # consumed by services/fetcher (POST /v1/extract)
  triage/         # consumed by orchestrators.defs.triage_knowledge_queue
  wiki/           # consumed by workflows.wiki_synthesis (extract_claims.py, extract_entities.py, entity_assignment.py / orchestrators.defs.fetch_extract_queue)
  # future: knowledge_graph/, etc.
````

## Resolution

The `wiki/`, `triage/` and `eval/` subdirs resolve via the `KP_PROMPTS_ROOT` env var, defaulting to the repo-root `prompts/` directory (used by evals + tests to point at alternate trees, not by deployments).

`extraction/` is the exception: it is read by the standalone `services/fetcher` process, not by anything in this uv workspace. The fetcher resolves it via its own `FETCHER_EXTRACTION_PROMPTS_ROOT` setting (default `prompts`, relative to the service's working directory — where the Docker image copies the tree), deliberately not aliased to `KP_PROMPTS_ROOT`: compose passes the shared `.env` into the fetcher container, so a laptop path set there for an eval run would follow into the image and point at nothing.

## Version-naming convention

`v<N>_<content_type>_<source-tag>_<YYYY_MM_DD>.md`

- `v<N>` — major version of the prompt schema
- `<content_type>` — `youtube`, `arxiv`, `article`, etc.
- `<source-tag>` — short tag identifying the working copy / experiment (e.g., `kp_copy`)
- `<YYYY_MM_DD>` — landing date

Example: `v5_youtube_kp_copy_2026_06_01.md`

Sub-prompt files used by multi-call extractors (e.g., `narrative_v1.md`, `topic_card_v1.md`, `followups_v1.md`) use a simpler `<role>_v<N>.md` form.

## Future direction

Alignment with `newsletter-assistant`'s `packages/core/src/core/prompts/` pattern (loader + assembly framework) is a deferred follow-up. For now: flat per-domain subdirs, file-based loading.
