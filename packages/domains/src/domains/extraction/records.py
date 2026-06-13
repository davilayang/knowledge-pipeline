"""In-memory containers passed between the extractor and the queue_store writer.

Schemas in `schemas.py` are the cross-repo contract (mirrored byte-for-byte
in newsletter-assistant). The records here are kp-internal — NA never sees
them; they hop from `ThreeCallOpenAIExtractor.extract()` into
`queue_store.sources.record_extraction_calls()` and die there.

Plain `@dataclass` instead of pydantic — these are write-only carriers, no
validation needed (the schema-validated content is in `output` already).
"""

from dataclasses import dataclass
from typing import Any


@dataclass
class ExtractionCallRecord:
    """One LLM call's output + provenance, ready for one INSERT into
    `extraction_calls`. Multiple records per (notion_page_id, call_kind) are
    allowed — the AUTOINCREMENT id leaves room for LangGraph refinement loops."""

    call_kind: str
    """narrative | topic_card | followups | (LangGraph: planner | critic | …)"""

    prompt_label: str
    prompt_sha256: str
    schema_name: str | None
    """`TopicCard` / `Followups` for structured calls; None for narrative (plain text)."""

    output: str
    """Markdown for narrative; pydantic-JSON (`model_dump_json()`) for structured."""

    tokens_in: int
    tokens_out: int
    cached_tokens: int | None
    """OpenAI prefix-cache hits; nullable when the SDK didn't report it."""

    duration_ms: float
    extracted_at: str
    """ISO-8601 UTC."""

    node_metadata: dict[str, Any] | None = None
    """LangGraph extras (node_id, parent_node_id, revision_count). Serialised
    to JSON on write; None for the v1 three-call shape."""

    prompt_set_shape: str | None = None
    """Which content_shape's PromptBundle the extractor selected for this
    call. NULL on rows written before the column existed; downstream eval
    queries should coalesce to `"unknown"` for grouping."""
