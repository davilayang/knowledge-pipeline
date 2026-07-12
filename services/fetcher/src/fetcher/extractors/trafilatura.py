"""HTML to markdown via trafilatura."""

from typing import Any

import trafilatura

from fetcher.metadata import build_metadata


def extract_metadata(html: str) -> dict[str, Any]:
    """Provenance (title / author / published date) parsed from the SAME HTML the
    content extraction reads — free, no extra network call. Trafilatura's `.date`
    is already normalized to `YYYY-MM-DD`; a field it can't find stays absent."""
    if not html:
        return {}
    doc = trafilatura.extract_metadata(html)
    if doc is None:
        return {}
    return build_metadata(title=doc.title, authors=doc.author, published=doc.date)


def extract(html: str) -> str:
    """Extract main content from HTML. Empty string on failure."""
    if not html:
        return ""
    result = trafilatura.extract(
        html,
        output_format="markdown",
        include_links=True,
        include_images=True,
        include_tables=True,
        deduplicate=True,
    )
    return result or ""
