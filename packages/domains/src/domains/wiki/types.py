from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

EntityType = Literal[
    "concept", "tool", "trend", "person", "organization", "method", "dataset", "other"
]


class WikiPage(BaseModel):
    """A wiki page with YAML frontmatter fields and markdown content."""

    entity_id: str = Field(description="Opaque surrogate id (e_<16hex>), minted once")
    title: str = Field(description="Human-readable page title")
    entity_type: EntityType = Field(description="Entity category")
    summary: str = Field(
        default="",
        description="One-sentence document-shape summary; names the entity directly.",
    )
    related: list[str] = Field(
        default_factory=list,
        description=(
            "Entity IDs co-extracted from the SAME triggering article (synthesis "
            "siblings), or the LLM's `related:` frontmatter if it emits one. "
            "Per-synthesis and overwritten each tick — NOT a curated, accumulated "
            "related-pages set."
        ),
    )
    sources: list[str] = Field(
        default_factory=list,
        description=(
            "Defaults to the single triggering item id (`[source_id]`); the LLM may "
            "override via `sources:` frontmatter (unreliable). NOT authoritative or "
            "accumulated — the page's true source count is derived on read via "
            "attributed.count_sources_for_entity."
        ),
    )
    updated_at: date = Field(description="Last update date")
    content: str = Field(description="Markdown body (below frontmatter)")


# --- LLM extraction schemas (Call 1) ---


class ExtractedEntity(BaseModel):
    """A single entity the LLM identified in an article.

    The LLM never mints an id — it proposes a display name + category, and
    optionally `matched_id` (the `e_<hex>` of an entity in the known-entities
    snapshot it judges to be the SAME thing). The resolver assigns the surrogate
    id (reuse-or-mint); the slug is system-generated from the title.
    """

    title: str = Field(description="Canonical display name of the entity")
    entity_type: EntityType = Field(description="Category of this entity")
    matched_id: str | None = Field(
        default=None,
        description=(
            "If this entity is the SAME as one in the known-entities list, copy "
            "its e_<hex> id here verbatim; otherwise null. Never invent an id."
        ),
    )
    aliases: list[str] = Field(
        default_factory=list, description="Other names / acronyms this entity is known by"
    )


class ExtractionResult(BaseModel):
    """Structured output from Call 1: entity extraction."""

    entities: list[ExtractedEntity] = Field(
        max_length=15, description="Key entities found in the article (max 15)"
    )
