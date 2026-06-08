"""Source registry: maps URLs to source modules."""

from fetcher.sources import article, arxiv, youtube
from fetcher.types import Source


REGISTERED_SOURCES: list[Source] = [arxiv, youtube, article]  # type: ignore[list-item]


def find_source(url: str) -> Source | None:
    """Find the first source that matches the given URL."""
    for source in REGISTERED_SOURCES:
        if source.matches(url):
            return source
    return None
