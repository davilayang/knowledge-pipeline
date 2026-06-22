# `prompts/wiki/`

Prompt assets for the wiki-synthesis pipeline, loaded by
`workflows.wiki_synthesis.prompts` (consumed by `synthesize.py` /
`orchestrators.defs.synthesize_wiki`).

## Files

| Constant (`prompts.py`) | File | Role |
|---|---|---|
| `ENTITY_EXTRACTION_SYSTEM` | `entity_extraction_system_v1.md` | Call #1 system prompt — pick entities + `matched_id` |
| `ENTITY_EXTRACTION_USER` | `entity_extraction_user_v1.md` | Call #1 user template (`{known_entities}` / `{title}` / `{article_text}`) |
| `PAGE_SYNTHESIS_SYSTEM` | `page_synthesis_system_v1.md` | Call #2 system prompt — merge/update the page |
| `PAGE_SYNTHESIS_USER_CREATE` | `page_synthesis_user_create_v1.md` | Call #2 user template, new page |
| `PAGE_SYNTHESIS_USER_UPDATE` | `page_synthesis_user_update_v1.md` | Call #2 user template, existing page |

The page-synthesis USER templates **lead with the shared article block** and
trail the per-entity fields — within one item every entity is synthesised
against the same article, so `[system + article]` is a constant prefix OpenAI's
prompt caching reuses across the entity loop. Don't move the per-entity fields
to the top.

To iterate: edit the markdown; add a `_v2` file + point the `prompts.py`
constant at it in the same commit. Versioned filenames keep A/B evals trivial.

## Resolution

`KP_PROMPTS_ROOT` env var → see [`../README.md`](../README.md).
