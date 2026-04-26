# Wiki page types and LLM extraction schemas.

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

PageType = Literal["concept", "tool", "trend"]


class WikiPage(BaseModel):
    """A wiki page with YAML frontmatter fields and markdown content."""

    entity_id: str = Field(description="Stable ID in format: {page_type}__{slug}")
    title: str = Field(description="Human-readable page title")
    page_type: PageType = Field(description="Page category")
    related: list[str] = Field(default_factory=list, description="Entity IDs of related pages")
    sources: list[str] = Field(default_factory=list, description="Content IDs of source articles")
    updated_at: date = Field(description="Last update date")
    content: str = Field(description="Markdown body (below frontmatter)")


# --- LLM extraction schemas (Call 1) ---


class ExtractedEntity(BaseModel):
    """A single entity identified in an article."""

    entity_id: str = Field(description="Existing ID from aliases or new {page_type}__{slug}")
    title: str = Field(description="Canonical entity name")
    page_type: PageType = Field(description="Category of this entity")
    is_new: bool = Field(description="True if this entity is not in aliases.yaml")
    aliases: list[str] = Field(default_factory=list, description="Known aliases for new entities")


class ExtractionResult(BaseModel):
    """Structured output from Call 1: entity extraction."""

    entities: list[ExtractedEntity] = Field(
        max_length=10, description="Key entities found in the article (max 10)"
    )
