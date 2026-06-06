"""Stateless OpenAI extraction primitives.

Consumed by:
- orchestrators.defs.extract_complex_contents (production extraction Dagster asset)
- evals.extraction (variant comparison + benchmark)
- packages/evals/notebooks/ (workbench iteration)

Prompts live at repo-root `prompts/extraction/`; loaders pass `prompt_text: str`
to extractor constructors. This module does NOT resolve prompts from files —
that's an orchestration concern (see orchestrators.defs.extract_complex_contents.resources).
"""

from workflows.extraction.protocol import ExtractionUsage, ExtractorProtocol
from workflows.extraction.three_call_openai import ThreeCallOpenAIExtractor

__all__ = [
    "ExtractionUsage",
    "ExtractorProtocol",
    "ThreeCallOpenAIExtractor",
]
