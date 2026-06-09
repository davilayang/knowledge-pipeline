"""Tests for source matching and tier metadata."""

from fetcher.extractors.jina import wraps_upstream_error as _jina_wraps_upstream_error
from fetcher.handlers import article, arxiv, youtube


def test_article_matches_generic_http_only() -> None:
    assert article.matches("https://example.com/article") is True
    assert article.matches("https://arxiv.org/abs/2401.00001") is False
    assert article.matches("https://www.youtube.com/watch?v=dQw4w9WgXcQ") is False
    assert article.matches("https://example.com/paper.pdf") is False


def test_arxiv_matches_abs_and_pdf() -> None:
    assert arxiv.matches("https://arxiv.org/abs/2401.00001") is True
    assert arxiv.matches("https://arxiv.org/pdf/2401.00001v2.pdf") is True
    assert arxiv.matches("https://example.com/x") is False


def test_youtube_extracts_video_ids() -> None:
    assert youtube.extract_video_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert youtube.extract_video_id("https://youtu.be/dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert youtube.extract_video_id("https://www.youtube.com/shorts/abcdefghijk") == "abcdefghijk"
    assert youtube.extract_video_id("https://example.com") is None


def test_tier_order_and_strict_flags() -> None:
    assert [tier.name for tier in article.TIERS] == ["jina", "curl_cffi"]
    assert [tier.name for tier in arxiv.TIERS] == ["pymupdf4llm", "llamaparse"]
    assert [tier.name for tier in youtube.TIERS] == ["transcript_api"]
    assert article.STRICT_PAID_TIER is False
    assert arxiv.STRICT_PAID_TIER is True
    assert youtube.STRICT_PAID_TIER is False


async def test_article_jina_4xx_returns_empty_content() -> None:
    from unittest.mock import AsyncMock, MagicMock

    from fetcher.handlers.article import _jina_fetch

    ctx = MagicMock()
    response = MagicMock()
    response.status_code = 401
    response.text = '{"message":"Invalid API key"}'
    ctx.jina_client.get = AsyncMock(return_value=response)

    result = await _jina_fetch(ctx, "https://example.com")
    assert result.status == 401
    assert result.content == ""


def test_jina_upstream_error_marker_detection() -> None:
    """Jina wraps upstream 4xx/5xx in 200 with a 'Warning:' marker — detect it."""
    assert _jina_wraps_upstream_error(
        "Title: 404\n\nWarning: Target URL returned error 404: Not Found\n\nMarkdown Content:\n"
    )
    assert _jina_wraps_upstream_error(
        "Warning: Target URL returned error 500: Internal Server Error"
    )
    assert not _jina_wraps_upstream_error("Title: Real Article\n\nSome legitimate prose.")
    assert not _jina_wraps_upstream_error("")


async def test_article_jina_demotes_upstream_404_wrapper() -> None:
    """HTTP 200 from Jina but body carries the upstream-error marker → empty content."""
    from unittest.mock import AsyncMock, MagicMock

    from fetcher.handlers.article import _jina_fetch

    ctx = MagicMock()
    response = MagicMock()
    response.status_code = 200
    response.text = (
        "Title: 404\n\nURL Source: https://example.com/gone\n\n"
        "Warning: Target URL returned error 404: Not Found\n\n"
        "Markdown Content:\nsome boilerplate footer\n"
    )
    ctx.jina_client.get = AsyncMock(return_value=response)

    result = await _jina_fetch(ctx, "https://example.com/gone")
    assert result.status == 200
    assert result.content == ""
