# `workflows/extraction/`

Stateless OpenAI extraction primitives. `ThreeCallOpenAIExtractor.extract(content, *, content_type, content_shape, user_notes=None)` returns an `(ExtractionPayload, list[ExtractionCallRecord])` tuple via three OpenAI Chat Completions calls.

## Public API

````python
from workflows.extraction import (
    ThreeCallOpenAIExtractor,    # three calls (narrative → topic_card → followups)
    ExtractorProtocol,           # structural contract for extractor strategies
    ExtractionUsage,             # token usage dataclass
    PromptBundle,                # per-content-shape (text, label) role triple, passed via prompt_sets
)
````

`ThreeCallOpenAIExtractor` is the v2 strategy and the only production extractor. A v1 `SingleShotOpenAIExtractor` existed in earlier revisions; it has been removed. Reviving a single-shot path would be a few lines on top of `workflows.llm.generate_structured_with_usage(schema=TopicCard)` if ever needed.

## Prompt-loading contract

`ThreeCallOpenAIExtractor` accepts a `prompt_sets: dict[str, PromptBundle]` argument at construction — one `PromptBundle` per `content_shape`, each holding the three `(text, label)` role pairs. **It does NOT resolve prompts from files or env vars.**

Prompt resolution is an orchestration concern. Production resolves via `orchestrators.defs.fetch_extract_queue.resources.ExtractorRegistry`, which reads markdown from repo-root `prompts/extraction/` using the label constants in `def_config.py` (`PROMPT_LABEL_NARRATIVE`, `PROMPT_LABEL_TOPIC_CARD`, `PROMPT_LABEL_FOLLOWUPS`). The `KP_PROMPTS_ROOT` env var overrides the prompts root (used by evals + tests, not deployments).

Evals and notebooks resolve prompts directly via variant config (see `evals/extraction/variants.py`).

## Why this lives in `workflows/`

`workflows/` is the home for LLM-calling code (alongside `wiki_synthesis/`, `costs.py`, `llm.py`). The `fetch_extract_queue/` Dagster wiring stays in `orchestrators/`; the LLM call itself is workflow-layer concern. This split lets `evals` depend on `workflows` (which it already does) and reuse the production extractor for variant comparison without a circular dep.
