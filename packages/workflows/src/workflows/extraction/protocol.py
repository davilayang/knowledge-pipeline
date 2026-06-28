"""Contract every extractor strategy must satisfy.

Strategies map a (content, content_type) pair to (Topic Card dict, usage).
The contract is intentionally narrow — single-shot HTTP, multi-step
LangGraph workflows, and hybrid approaches all satisfy it the same way.
"""

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass
class ExtractionUsage:
    input_tokens: int
    output_tokens: int


class ExtractorProtocol(Protocol):
    def extract(
        self,
        content: str,
        *,
        content_type: str,
        content_shape: str,
        user_notes: str | None = None,
    ) -> tuple[dict[str, Any], ExtractionUsage]: ...
