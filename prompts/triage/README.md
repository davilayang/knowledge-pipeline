# `prompts/triage/`

Prompt assets consumed by `orchestrators.defs.triage_knowledge_queue`. Each file's first paragraph explains what it does.

## Active in production (loaded by `ContentShapeClassifier`)

| Label (`def_config.py`) | File | Used by |
|---|---|---|
| `CONTENT_SHAPE_CLASSIFIER_PROMPT = "content_shape_classifier_v1"` | `content_shape_classifier_v1.md` | `ContentShapeClassifier.classify()` system prompt — the LLM-primary content_shape resource |

To iterate the prompt: edit the markdown file AND bump the label constant in the same commit (e.g. add `_v2`). Versioned filenames keep A/B evals trivial.

## Resolution

`KP_PROMPTS_ROOT` env var → see [`../README.md`](../README.md).
