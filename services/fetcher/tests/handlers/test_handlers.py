"""Tests for source matching and tier metadata."""

from fetcher.extractors.jina import wraps_upstream_error as _jina_wraps_upstream_error
from fetcher.handlers import article, arxiv, pdf, youtube


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
    assert [tier.name for tier in article.TIERS] == ["jina", "curl_cffi", "tavily"]
    assert [tier.name for tier in arxiv.TIERS] == ["pymupdf4llm", "llamaparse"]
    assert [tier.name for tier in youtube.TIERS] == ["transcript_api"]
    assert article.STRICT_PAID_TIER is False
    assert arxiv.STRICT_PAID_TIER is True
    assert youtube.STRICT_PAID_TIER is False


def test_article_tavily_tier_metadata() -> None:
    tavily_tier = next(tier for tier in article.TIERS if tier.name == "tavily")
    assert tavily_tier.cost == "paid"
    assert tavily_tier.rate_limit_key == "tavily"


async def test_article_tavily_skipped_when_key_unset() -> None:
    from unittest.mock import MagicMock

    from fetcher.handlers.article import _tavily_fetch

    ctx = MagicMock()
    ctx.tavily_api_key = None

    result = await _tavily_fetch(ctx, "https://example.com")
    assert result.content == ""
    assert result.status == 0


async def test_article_tavily_calls_extractor_when_key_set() -> None:
    from unittest.mock import AsyncMock, MagicMock, patch

    from fetcher.handlers.article import _tavily_fetch

    ctx = MagicMock()
    ctx.tavily_api_key = "k"
    with patch("fetcher.handlers.article.tavily_extractor.extract", new=AsyncMock(return_value="# md")):
        result = await _tavily_fetch(ctx, "https://example.com")

    assert result.content == "# md"
    assert result.status == 200


def test_pdf_matches_pdf_url_not_arxiv() -> None:
    assert pdf.matches("https://example.com/paper.pdf") is True
    assert pdf.matches("https://example.com/path/file.PDF") is True
    assert pdf.matches("https://arxiv.org/pdf/2401.00001v2.pdf") is False
    assert pdf.matches("https://export.arxiv.org/pdf/2401.00001.pdf") is False
    assert pdf.matches("https://example.com/article") is False
    assert pdf.matches("mailto:x@y.com") is False


def test_pdf_strict_paid_tier_is_false() -> None:
    assert pdf.STRICT_PAID_TIER is False


def test_pdf_tier_order_is_pymupdf_then_llamaparse() -> None:
    assert [tier.name for tier in pdf.TIERS] == ["pymupdf4llm", "llamaparse"]
    paid = next(tier for tier in pdf.TIERS if tier.name == "llamaparse")
    assert paid.cost == "paid"
    assert paid.rate_limit_key == "llamaparse"


async def test_pdf_free_tier_reads_bytes_then_calls_pymupdf_extractor() -> None:
    from unittest.mock import AsyncMock, MagicMock, patch

    from fetcher.handlers.pdf import _pymupdf4llm_fetch

    ctx = MagicMock()
    ctx.default_timeout_s = 30
    response = MagicMock()
    response.status_code = 200
    response.content = b"%PDF-1.4 fake bytes"
    ctx.http_client.get = AsyncMock(return_value=response)

    with patch(
        "fetcher.handlers.pdf.pymupdf_extractor.to_markdown", return_value="# md"
    ) as render:
        result = await _pymupdf4llm_fetch(ctx, "https://example.com/paper.pdf")

    render.assert_called_once_with(b"%PDF-1.4 fake bytes")
    assert result.content == "# md"
    assert result.status == 200


async def test_pdf_free_tier_caps_download_at_max_bytes() -> None:
    from unittest.mock import AsyncMock, MagicMock, patch

    from fetcher.handlers.pdf import MAX_PDF_BYTES, _pymupdf4llm_fetch

    ctx = MagicMock()
    ctx.default_timeout_s = 30
    response = MagicMock()
    response.status_code = 200
    response.content = b"x" * (MAX_PDF_BYTES + 1024)
    ctx.http_client.get = AsyncMock(return_value=response)

    with patch(
        "fetcher.handlers.pdf.pymupdf_extractor.to_markdown", return_value="ok"
    ) as render:
        await _pymupdf4llm_fetch(ctx, "https://example.com/big.pdf")

    passed = render.call_args.args[0]
    assert len(passed) == MAX_PDF_BYTES


async def test_pdf_paid_tier_calls_llamaparse_render_pdf() -> None:
    from unittest.mock import AsyncMock, MagicMock, patch

    from fetcher.handlers.pdf import _llamaparse_fetch

    ctx = MagicMock()
    ctx.llama_parse_api_key = "k"
    ctx.llama_parse_tier_pdf = "agentic_plus"

    with patch(
        "fetcher.handlers.pdf.llamaparse_extractor.render_pdf",
        new=AsyncMock(return_value="# heavy md"),
    ) as render:
        result = await _llamaparse_fetch(ctx, "https://example.com/paper.pdf")

    render.assert_called_once_with(
        ctx.http_client,
        pdf_url="https://example.com/paper.pdf",
        api_key="k",
        tier="agentic_plus",
    )
    assert result.content == "# heavy md"
    assert result.status == 200


async def test_pdf_paid_tier_demotes_extractor_failure() -> None:
    from unittest.mock import AsyncMock, MagicMock, patch

    from fetcher.handlers.pdf import _llamaparse_fetch

    ctx = MagicMock()
    ctx.llama_parse_api_key = "k"
    ctx.llama_parse_tier_pdf = "agentic_plus"
    with patch(
        "fetcher.handlers.pdf.llamaparse_extractor.render_pdf",
        new=AsyncMock(side_effect=ValueError("boom")),
    ):
        result = await _llamaparse_fetch(ctx, "https://example.com/paper.pdf")

    assert result.content == ""
    assert result.status == 0


async def test_article_tavily_demotes_extractor_failure() -> None:
    from unittest.mock import AsyncMock, MagicMock, patch

    from fetcher.handlers.article import _tavily_fetch

    ctx = MagicMock()
    ctx.tavily_api_key = "k"
    with patch(
        "fetcher.handlers.article.tavily_extractor.extract",
        new=AsyncMock(side_effect=ValueError("Tavily extract HTTP 500: boom")),
    ):
        result = await _tavily_fetch(ctx, "https://example.com")

    assert result.content == ""
    assert result.status == 0


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
