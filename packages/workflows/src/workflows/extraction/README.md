# `workflows/extraction/`

Stateless OpenAI extraction primitives. Produces a Topic Card dict from `(content: str, content_type: str)` via one or three OpenAI Chat Completions calls.

## Public API

````python
from workflows.extraction import (
    ThreeCallOpenAIExtractor,    # three calls (narrative → topic_card → followups)
    ExtractorProtocol,           # structural contract for extractor strategies
    ExtractionUsage,             # token usage dataclass
)
````

`ThreeCallOpenAIExtractor` is the v2 strategy and the only production extractor. A v1 `SingleShotOpenAIExtractor` existed in earlier revisions; it has been removed. Reviving a single-shot path would be a few lines on top of `workflows.llm.generate_structured_with_usage(schema=TopicCard)` if ever needed.

## Prompt-loading contract

`ThreeCallOpenAIExtractor` accepts per-role `*_prompt: str` arguments at construction. **It does NOT resolve prompts from files or env vars.**

Prompt resolution is an orchestration concern. Production resolves via `orchestrators.defs.extract_complex_contents.resources.ExtractorRegistry`, which reads markdown from repo-root `prompts/extraction/` using the label constants in `def_config.py` (`PROMPT_LABEL_NARRATIVE`, `PROMPT_LABEL_TOPIC_CARD`, `PROMPT_LABEL_FOLLOWUPS`). The `KP_PROMPTS_ROOT` env var overrides the prompts root (used by evals + tests, not deployments).

Evals and notebooks resolve prompts directly via variant config (see `evals/extraction/variants.py`).

## Why this lives in `workflows/`

`workflows/` is the home for LLM-calling code (alongside `wiki_synthesis/`, `costs.py`, `llm.py`). The `extract_complex_contents/` Dagster wiring stays in `orchestrators/`; the LLM call itself is workflow-layer concern. This split lets `evals` depend on `workflows` (which it already does) and reuse the production extractor for variant comparison without a circular dep.
