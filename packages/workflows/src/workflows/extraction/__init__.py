"""Stateless OpenAI extraction primitives.

Consumed by:
- orchestrators.defs.fetch_extract_queue (production extraction Dagster asset)
- evals.extraction (variant comparison + benchmark)
- packages/evals/notebooks/ (workbench iteration)

Prompts live at repo-root `prompts/extraction/`; loaders pass `prompt_text: str`
to extractor constructors. This module does NOT resolve prompts from files —
that's an orchestration concern (see orchestrators.defs.fetch_extract_queue.resources).
"""

from workflows.extraction.protocol import ExtractionUsage, ExtractorProtocol
from workflows.extraction.three_call_openai import ThreeCallOpenAIExtractor
from workflows.extraction.types import PromptBundle

__all__ = [
    "ExtractionUsage",
    "ExtractorProtocol",
    "PromptBundle",
    "ThreeCallOpenAIExtractor",
]
