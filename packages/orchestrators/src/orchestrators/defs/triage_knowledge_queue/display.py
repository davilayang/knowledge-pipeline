"""Per-content-type display-meta resolvers for Notion's Name + Description.

YouTube's static HTML returns "- YouTube" (video title is JS-rendered) and
sites with site-wide og: tags give generic descriptions. Picking the best
source per content_type from the enrichment signals (already fetched by the
`enriched` asset) avoids those landmines.
"""

from .classify import (
    CONTENT_TYPE_ARTICLE,
    CONTENT_TYPE_ARXIV,
    CONTENT_TYPE_YOUTUBE,
)
from .enrich import EnrichmentSignals


def resolve_display_title(*, content_type: str, enrichment: EnrichmentSignals) -> str | None:
    """Return the title to seed Notion's Name from, or None if no usable
    source is available for this content_type."""
    match content_type:
        case t if t == CONTENT_TYPE_YOUTUBE:
            return enrichment.youtube.title if enrichment.youtube else None
        case t if t == CONTENT_TYPE_ARXIV:
            return enrichment.arxiv.title if enrichment.arxiv else None
        case t if t == CONTENT_TYPE_ARTICLE:
            return enrichment.article.title if enrichment.article else None
        case _:
            return None


def resolve_display_description(*, content_type: str, enrichment: EnrichmentSignals) -> str | None:
    """Return the description to seed Notion's Description from, or None
    if no usable source is available for this content_type."""
    match content_type:
        case t if t == CONTENT_TYPE_YOUTUBE:
            return None
        case t if t == CONTENT_TYPE_ARTICLE:
            return enrichment.article.description if enrichment.article else None
        case t if t == CONTENT_TYPE_ARXIV:
            return enrichment.arxiv.abstract if enrichment.arxiv else None
        case _:
            return None
