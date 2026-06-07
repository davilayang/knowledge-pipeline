"""Tests for source matching and tier metadata."""

from fetcher.sources import article, arxiv, youtube


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

    from fetcher.sources.article import _jina_fetch

    ctx = MagicMock()
    response = MagicMock()
    response.status_code = 401
    response.text = '{"message":"Invalid API key"}'
    ctx.jina_client.get = AsyncMock(return_value=response)

    result = await _jina_fetch(ctx, "https://example.com")
    assert result.status == 401
    assert result.content == ""
