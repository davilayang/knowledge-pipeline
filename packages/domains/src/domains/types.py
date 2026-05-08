"""Shared data types for the domain layer.

`IngestItem` is the normalized shape every source adapter yields. Pipelines
(synthesize_wiki, populate_vector_store) consume `list[IngestItem]` and don't
care which source produced them.

Optional fields (`author`, `url`, `started_at`) carry source-specific metadata
that some adapters expose and others don't — consumers read what they need.
"""

from dataclasses import dataclass
from datetime import date, datetime


@dataclass
class IngestItem:
    item_id: str
    title: str
    date: date | None
    text: str
    source_type: str  # e.g. "raw_store" | "local_file" | "sessions" | "research"
    source_ref: str  # e.g. "raw_store:content_123" or "local:notes.md"
    author: str | None = None
    url: str | None = None
    started_at: datetime | None = None


@dataclass
class Chunk:
    """A single chunk of content produced by a chunking strategy."""

    text: str
    heading: str  # nearest heading ancestor (empty if none)
    index: int  # position in the chunk sequence
