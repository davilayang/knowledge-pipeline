"""Shared data types for the domain layer.

`IngestItem` is the normalized shape every source adapter yields. Pipelines
(populate_vector_store, fetch_extract_queue attributed lane) consume
`list[IngestItem]` and don't care which source produced them.

Optional fields (`author`, `url`, `started_at`) carry source-specific metadata
that some adapters expose and others don't — consumers read what they need.
"""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol


@dataclass
class IngestItem:
    item_id: str
    title: str
    date: date | None
    text: str
    source_type: str  # e.g. "raw_store" | "local_file" | "sessions"
    source_ref: str  # e.g. "raw_store:content_123" or "local:notes.md"
    author: str | None = None
    url: str | None = None
    started_at: datetime | None = None
    num_sources: int | None = None  # wiki: distinct content items behind the entity (W3 gate)


class IngestSource(Protocol):
    """Protocol for ingest sources — pure generators."""

    def get_items(self) -> list[IngestItem]: ...
