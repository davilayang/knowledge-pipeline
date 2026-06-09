"""HTML to markdown via trafilatura."""

import trafilatura


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
