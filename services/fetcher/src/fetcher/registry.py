"""URL-handler registry: maps URLs to handler modules."""

from fetcher.handlers import article, arxiv, youtube
from fetcher.types import URLHandler


REGISTERED_HANDLERS: list[URLHandler] = [arxiv, youtube, article]  # type: ignore[list-item]


def find_handler(url: str) -> URLHandler | None:
    """Find the first handler that claims the given URL."""
    for handler in REGISTERED_HANDLERS:
        if handler.matches(url):
            return handler
    return None
