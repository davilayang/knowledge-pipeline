# `prompts/wiki/`

Prompt assets for the wiki-synthesis attributed lane, loaded by
`workflows.wiki_synthesis.prompts` (consumed by `extract_claims.py`,
`extract_entities.py`, `entity_assignment.py` /
`orchestrators.defs.fetch_extract_queue`).

## Files

| Constant (`prompts.py`) | File | Role |
|---|---|---|
| `EXTRACT_SHARED_SYSTEM` | `extract_shared_system_v1.md` | Shared system prompt for both extract-time calls |
| `EXTRACT_ARTICLE_ENVELOPE` | `extract_article_envelope_v1.md` | Article envelope — shared byte-identically by claims + entities calls so the article prompt-caches across them |
| `EXTRACT_CLAIMS_TASK` | `extract_claims_task_v1.md` | Task tail for the claims extraction call |
| `EXTRACT_ENTITIES_TASK` | `extract_entities_task_v1.md` | Task tail for the entity candidate extraction call |
| `SUBJECT_ATTRIBUTION_SYSTEM` | `subject_attribution_system_v1.md` | System prompt for the closed subject-attribution call |
| `SUBJECT_ATTRIBUTION_USER` | `subject_attribution_user_v1.md` | User template for the subject-attribution call |

The extract-time USER templates (`EXTRACT_ARTICLE_ENVELOPE`) **lead with the
article block** so `[system + article]` is a constant prefix OpenAI's prompt
caching reuses across both the claims and entities calls. Don't move the task
tail to the top.

To iterate: edit the markdown; add a `_v2` file + point the `prompts.py`
constant at it in the same commit. Versioned filenames keep A/B evals trivial.

## Resolution

`KP_PROMPTS_ROOT` env var → see [`../README.md`](../README.md).
