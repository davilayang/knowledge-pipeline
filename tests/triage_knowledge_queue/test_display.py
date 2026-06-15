"""Tests for the per-content-type display-meta resolvers.

Triage seeds Notion's Name + Description from URL meta. Static-HTML scrape
gives "- YouTube" for YouTube (JS-rendered title) and generic boilerplate
descriptions for sites with site-wide og: tags. The resolver here picks the
best source per content_type from the already-fetched enrichment signals.
"""

from orchestrators.defs.triage_knowledge_queue.display import (
    resolve_display_description,
    resolve_display_title,
)
from orchestrators.defs.triage_knowledge_queue.enrich import (
    ArticleSignals,
    ArxivSignals,
    EnrichmentSignals,
    YoutubeSignals,
)


def test_youtube_uses_oembed_title() -> None:
    enrichment = EnrichmentSignals(
        youtube=YoutubeSignals(channel="AI Engineer", title="How to ship a thing"),
    )

    title = resolve_display_title(content_type="YouTube", enrichment=enrichment)

    assert title == "How to ship a thing"


def test_youtube_without_oembed_returns_none() -> None:
    """oEmbed timed out / 404'd → no usable title. Return None so the
    asset leaves Notion Name alone, rather than seeding the bogus
    '- YouTube' static-HTML scrape."""
    enrichment = EnrichmentSignals(youtube=None)

    title = resolve_display_title(content_type="YouTube", enrichment=enrichment)

    assert title is None


def test_arxiv_uses_atom_title() -> None:
    enrichment = EnrichmentSignals(
        arxiv=ArxivSignals(title="Attention Is All You Need", abstract="..."),
    )

    title = resolve_display_title(content_type="arXiv", enrichment=enrichment)

    assert title == "Attention Is All You Need"


def test_article_uses_article_signal_title() -> None:
    """Today ArticleSignals.title === url_meta.title (built by enrich.py
    from the same fetch_url_meta call); routing through the signal gives
    us the seam to swap in a better source (trafilatura article-extracted
    h1, OG title, etc.) without touching the asset wiring."""
    enrichment = EnrichmentSignals(
        article=ArticleSignals(
            redirected_url="https://itnext.io/x",
            title="LLMs likes C4 Diagrams",
            description="...",
        ),
    )

    title = resolve_display_title(content_type="Article", enrichment=enrichment)

    assert title == "LLMs likes C4 Diagrams"


def test_unenriched_content_type_returns_none() -> None:
    """Podcast / Other have no enrichment path today — resolver returns
    None and the asset falls back to leaving Notion Name alone."""
    title = resolve_display_title(content_type="Podcast", enrichment=EnrichmentSignals())

    assert title is None


# ---------------- description ----------------


def test_youtube_description_returns_none() -> None:
    """oEmbed doesn't return a video description, and the static HTML
    fetch only sees YouTube's site-wide og:description boilerplate
    ('Enjoy the videos and music you love...'). Better to leave Notion's
    Description blank than to surface that."""
    enrichment = EnrichmentSignals(
        youtube=YoutubeSignals(channel="AI Engineer", title="How to ship a thing"),
    )

    description = resolve_display_description(content_type="YouTube", enrichment=enrichment)

    assert description is None


def test_article_description_uses_article_signal() -> None:
    enrichment = EnrichmentSignals(
        article=ArticleSignals(
            redirected_url="https://itnext.io/x",
            title="LLMs likes C4 Diagrams",
            description="After 25 years of drawing boxes...",
        ),
    )

    description = resolve_display_description(content_type="Article", enrichment=enrichment)

    assert description == "After 25 years of drawing boxes..."


def test_arxiv_description_uses_abstract() -> None:
    """ArxivSignals.abstract is what the Atom API returns as <summary>;
    surface it as Notion's Description so the user sees the real paper
    abstract, not a generic site-wide blurb."""
    enrichment = EnrichmentSignals(
        arxiv=ArxivSignals(
            title="Attention Is All You Need",
            abstract="The dominant sequence transduction models...",
        ),
    )

    description = resolve_display_description(content_type="arXiv", enrichment=enrichment)

    assert description == "The dominant sequence transduction models..."
