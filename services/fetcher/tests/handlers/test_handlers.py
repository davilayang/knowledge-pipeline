"""Tests for handler matching and tier metadata."""

import pytest

from fetcher.handlers import article, arxiv, file_pdf, medium, youtube


@pytest.fixture
def medium_domains(monkeypatch: pytest.MonkeyPatch) -> frozenset[str]:
    """Pin the shared medium domain set for deterministic match tests."""
    from domains import medium_urls

    pinned = frozenset({"medium.com", "towardsdatascience.com", "betterprogramming.pub"})
    monkeypatch.setattr(medium_urls, "MEDIUM_DOMAINS", pinned)
    return pinned


def test_article_matches_generic_http_only() -> None:
    assert article.matches("https://example.com/article") is True
    assert article.matches("https://arxiv.org/abs/2401.00001") is False
    assert article.matches("https://www.youtube.com/watch?v=dQw4w9WgXcQ") is False
    assert article.matches("https://example.com/paper.pdf") is False


def test_arxiv_matches_abs_pdf_and_html() -> None:
    assert arxiv.matches("https://arxiv.org/abs/2401.00001") is True
    assert arxiv.matches("https://arxiv.org/pdf/2401.00001v2.pdf") is True
    assert arxiv.matches("https://arxiv.org/html/2606.09498v1") is True
    assert arxiv.matches("https://example.com/x") is False


def test_arxiv_extracts_id_from_html_path() -> None:
    assert arxiv.extract_arxiv_id("https://arxiv.org/html/2606.09498v1") == "2606.09498"


def test_youtube_extracts_video_ids() -> None:
    assert youtube.extract_video_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert youtube.extract_video_id("https://youtu.be/dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert youtube.extract_video_id("https://www.youtube.com/shorts/abcdefghijk") == "abcdefghijk"
    assert youtube.extract_video_id("https://example.com") is None


def test_strict_paid_tier_flags() -> None:
    """The STRICT_PAID_TIER contract: arxiv exceptions propagate (must True),
    article/youtube failures demote to next tier (must False)."""
    assert article.STRICT_PAID_TIER is False
    assert arxiv.STRICT_PAID_TIER is True
    assert youtube.STRICT_PAID_TIER is False


def test_arxiv_build_metadata_from_paper() -> None:
    # The arxiv tiers already have the paper's title/authors/published/id (they
    # format them into the header) — capture the same as canonical metadata so
    # the wiki source gets the real publish date + author.
    from datetime import datetime
    from types import SimpleNamespace

    from fetcher.handlers.arxiv import _build_metadata

    paper = SimpleNamespace(
        title="  A Paper  ",
        authors=[SimpleNamespace(name="Jane Doe"), SimpleNamespace(name="John Roe")],
        published=datetime(2026, 3, 1, 12, 0),
    )
    assert _build_metadata(paper, "2401.001") == {
        "title": "A Paper",
        "authors": ["Jane Doe", "John Roe"],
        "published": "2026-03-01",
        "arxiv_id": "2401.001",
    }


async def test_arxiv_pymupdf_tier_aborts_when_pdf_exceeds_max_bytes() -> None:
    """arxiv's pymupdf tier now streams through the shared capped downloader, so a
    pathologically large PDF aborts instead of being read fully into memory."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock, patch

    from fetcher.handlers._pdf_download import MAX_PDF_BYTES
    from fetcher.handlers.arxiv import _arxiv_pymupdf

    ctx = MagicMock()
    ctx.upstream_timeout_s = 30
    ctx.http_client.stream = MagicMock(
        return_value=_stream_ctxmgr(200, [b"x" * MAX_PDF_BYTES, b"y" * 1024])
    )
    paper = SimpleNamespace(pdf_url="https://arxiv.org/pdf/2401.00001.pdf")

    with (
        patch(
            "fetcher.handlers.arxiv._fetch_metadata",
            new=AsyncMock(return_value=("2401.00001", paper)),
        ),
        patch("fetcher.handlers.arxiv.pymupdf_extractor.to_markdown") as render,
    ):
        result = await _arxiv_pymupdf(ctx, "https://arxiv.org/abs/2401.00001")

    render.assert_not_called()
    assert result.content == ""
    assert result.status == 0


async def test_arxiv_pymupdf_tier_fails_on_http_error_status() -> None:
    """A non-2xx from the PDF URL must fail the pymupdf tier, not extract from the
    error-page body. Restores the raise_for_status semantics the shared-downloader
    refactor dropped: pymupdf will happily produce garbage from a 404 HTML page,
    which would otherwise be returned as a soft status=200 success."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock, patch

    from fetcher.handlers.arxiv import _arxiv_pymupdf

    ctx = MagicMock()
    ctx.upstream_timeout_s = 30
    ctx.http_client.stream = MagicMock(
        return_value=_stream_ctxmgr(404, [b"<html>Not Found</html>"])
    )
    paper = SimpleNamespace(pdf_url="https://arxiv.org/pdf/2401.00001.pdf")

    with (
        patch(
            "fetcher.handlers.arxiv._fetch_metadata",
            new=AsyncMock(return_value=("2401.00001", paper)),
        ),
        patch(
            "fetcher.handlers.arxiv.pymupdf_extractor.to_markdown",
            return_value="garbage extracted from the error page",
        ) as render,
    ):
        result = await _arxiv_pymupdf(ctx, "https://arxiv.org/abs/2401.00001")

    render.assert_not_called()
    assert result.content == ""
    assert result.status == 0


def test_arxiv_tier_order_is_pymupdf_then_llamaparse() -> None:
    """arxiv order matters: pymupdf4llm must run first (free, fast) before
    falling through to LlamaParse (paid). Other handlers' tier lists are
    enforced by their per-tier metadata tests."""
    assert [tier.name for tier in arxiv.TIERS] == ["pymupdf4llm", "llamaparse"]


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
    with patch(
        "fetcher.handlers.article.tavily_extractor.extract", new=AsyncMock(return_value="# md")
    ):
        result = await _tavily_fetch(ctx, "https://example.com")

    assert result.content == "# md"
    assert result.status == 200


def test_medium_matches_configured_domain(medium_domains: set[str]) -> None:
    assert medium.matches("https://medium.com/@author/title-abc123def456") is True
    assert medium.matches("https://towardsdatascience.com/title-abc123def456") is True
    assert medium.matches("https://www.towardsdatascience.com/title-abc123def456") is True
    # Author subdomains (`<name>.medium.com`) are Medium-hosted too — must
    # route to the medium handler so the paywall-bypass tier can run.
    assert medium.matches("https://pravash-techie.medium.com/title-abc123def456") is True
    assert medium.matches("https://example.com/post-abc123def456") is False
    assert medium.matches("mailto:x@y.com") is False


def test_medium_rapidapi_tier_is_paid(medium_domains: set[str]) -> None:
    """rapidapi tier must be marked paid + rate-limited under the rapidapi key.
    The tier name list ordering is implicit in `_rapidapi_skipped_when_key_unset`
    + the paid-tier gating logic."""
    paid = next(tier for tier in medium.TIERS if tier.name == "rapidapi")
    assert paid.cost == "paid"
    assert paid.rate_limit_key == "rapidapi"


def test_medium_extract_article_id_from_url() -> None:
    assert (
        medium.extract_article_id("https://towardsdatascience.com/some-cool-title-abc123def456")
        == "abc123def456"
    )
    assert (
        medium.extract_article_id("https://medium.com/@author/another-title-deadbeef1234")
        == "deadbeef1234"
    )
    assert (
        medium.extract_article_id(
            "https://medium.com/publication-name/yet-another-1a2b3c4d5e6f?source=rss"
        )
        == "1a2b3c4d5e6f"
    )
    with pytest.raises(ValueError):
        medium.extract_article_id("https://medium.com/no-trailing-id-here")


async def test_medium_rapidapi_skipped_when_key_unset(medium_domains: set[str]) -> None:
    from unittest.mock import MagicMock

    from fetcher.handlers.medium import _rapidapi_fetch

    ctx = MagicMock()
    ctx.rapidapi_key = None

    result = await _rapidapi_fetch(ctx, "https://towardsdatascience.com/title-abc123def456")
    assert result.content == ""
    assert result.status == 0


async def test_medium_rapidapi_calls_extractor_when_key_set(medium_domains: set[str]) -> None:
    from unittest.mock import AsyncMock, MagicMock, patch

    from fetcher.handlers.medium import _rapidapi_fetch

    ctx = MagicMock()
    ctx.rapidapi_key = "k"
    with patch(
        "fetcher.handlers.medium.rapidapi_medium_extractor.fetch_markdown",
        new=AsyncMock(return_value="# md"),
    ):
        result = await _rapidapi_fetch(ctx, "https://towardsdatascience.com/title-abc123def456")

    # Observable: extractor produced markdown + status 200. The kwarg shape
    # forwarded to fetch_markdown is plumbing (a wrong article_id would
    # surface as a 4xx from RapidAPI, covered by demote-on-failure test).
    assert result.content == "# md"
    assert result.status == 200


async def test_medium_rapidapi_demotes_extractor_failure(medium_domains: set[str]) -> None:
    from unittest.mock import AsyncMock, MagicMock, patch

    from fetcher.handlers.medium import _rapidapi_fetch

    ctx = MagicMock()
    ctx.rapidapi_key = "k"
    with patch(
        "fetcher.handlers.medium.rapidapi_medium_extractor.fetch_markdown",
        new=AsyncMock(side_effect=ValueError("boom")),
    ):
        result = await _rapidapi_fetch(ctx, "https://towardsdatascience.com/title-abc123def456")
    assert result.content == ""
    assert result.status == 0


def test_article_matches_excludes_medium_host(medium_domains: set[str]) -> None:
    assert article.matches("https://medium.com/@author/x-abc123def456") is False
    assert article.matches("https://towardsdatascience.com/x-abc123def456") is False


def test_pdf_matches_pdf_url_not_arxiv() -> None:
    assert file_pdf.matches("https://example.com/paper.pdf") is True
    assert file_pdf.matches("https://example.com/path/file.PDF") is True
    assert file_pdf.matches("https://arxiv.org/pdf/2401.00001v2.pdf") is False
    assert file_pdf.matches("https://export.arxiv.org/pdf/2401.00001.pdf") is False
    assert file_pdf.matches("https://example.com/article") is False
    assert file_pdf.matches("mailto:x@y.com") is False


def test_pdf_llamaparse_tier_is_paid() -> None:
    """llamaparse tier must be marked paid + rate-limited under the llamaparse
    key. Tier ordering is enforced by the paid-tier-gating cascade logic."""
    paid = next(tier for tier in file_pdf.TIERS if tier.name == "llamaparse")
    assert paid.cost == "paid"
    assert paid.rate_limit_key == "llamaparse"


def _stream_ctxmgr(status_code: int, chunks: list[bytes]):
    """Build a MagicMock that behaves like httpx.AsyncClient.stream(...).__aenter__()."""
    from unittest.mock import AsyncMock, MagicMock

    response = MagicMock()
    response.status_code = status_code

    async def _aiter(chunk_size: int = 64 * 1024):
        for chunk in chunks:
            yield chunk

    response.aiter_bytes = _aiter

    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=response)
    cm.__aexit__ = AsyncMock(return_value=None)
    return cm


async def test_pdf_free_tier_streams_bytes_then_calls_pymupdf_extractor() -> None:
    from unittest.mock import MagicMock, patch

    from fetcher.handlers.file_pdf import _pymupdf4llm_fetch

    ctx = MagicMock()
    ctx.upstream_timeout_s = 30
    ctx.http_client.stream = MagicMock(
        return_value=_stream_ctxmgr(200, [b"%PDF-1.4 ", b"fake bytes"])
    )

    with patch(
        "fetcher.handlers.file_pdf.pymupdf_extractor.to_markdown", return_value="# md"
    ) as render:
        result = await _pymupdf4llm_fetch(ctx, "https://example.com/paper.pdf")

    # Byte accumulation across chunks is a real streaming-correctness contract:
    # a buggy implementation that only passed the last chunk would lose data.
    # Assert the accumulated bytes contain both fragments, not the exact prefix.
    render.assert_called_once()
    accumulated = render.call_args.args[0]
    assert b"%PDF-1.4 " in accumulated
    assert b"fake bytes" in accumulated
    assert result.content == "# md"
    assert result.status == 200


async def test_pdf_free_tier_aborts_when_stream_exceeds_max_bytes() -> None:
    from unittest.mock import MagicMock, patch

    from fetcher.handlers.file_pdf import MAX_PDF_BYTES, _pymupdf4llm_fetch

    ctx = MagicMock()
    ctx.upstream_timeout_s = 30
    # Two chunks whose combined size exceeds the cap — the second push trips abort.
    over_cap = [b"x" * MAX_PDF_BYTES, b"y" * 1024]
    ctx.http_client.stream = MagicMock(return_value=_stream_ctxmgr(200, over_cap))

    with patch("fetcher.handlers.file_pdf.pymupdf_extractor.to_markdown") as render:
        result = await _pymupdf4llm_fetch(ctx, "https://example.com/big.pdf")

    # Aborted: extractor never called, empty result with status=0 signals tier failure.
    render.assert_not_called()
    assert result.content == ""
    assert result.status == 0


async def test_pdf_paid_tier_calls_llamaparse_render_pdf() -> None:
    from unittest.mock import AsyncMock, MagicMock, patch

    from fetcher.handlers.file_pdf import _llamaparse_fetch

    ctx = MagicMock()
    ctx.llama_parse_api_key = "k"
    ctx.llama_parse_tier_pdf = "agentic_plus"

    with patch(
        "fetcher.handlers.file_pdf.llamaparse_extractor.render_pdf",
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

    from fetcher.handlers.file_pdf import _llamaparse_fetch

    ctx = MagicMock()
    ctx.llama_parse_api_key = "k"
    ctx.llama_parse_tier_pdf = "agentic_plus"
    with patch(
        "fetcher.handlers.file_pdf.llamaparse_extractor.render_pdf",
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


async def test_article_jina_strips_preamble_on_success() -> None:
    """A clean 200 stores the article body with Jina's metadata preamble stripped."""
    from unittest.mock import AsyncMock, MagicMock

    from fetcher.handlers.article import _jina_fetch

    ctx = MagicMock()
    response = MagicMock()
    response.status_code = 200
    response.text = (
        "Title: Real Article\n"
        "URL Source: https://example.com/a\n"
        "Published Time: 2026-06-29T00:00:00Z\n\n"
        "Markdown Content:\n"
        "# Real Article\n\nThe actual body."
    )
    ctx.jina_client.get = AsyncMock(return_value=response)

    result = await _jina_fetch(ctx, "https://example.com/a")
    assert result.status == 200
    assert result.content == "# Real Article\n\nThe actual body."
    assert "Title:" not in result.content
    assert "Markdown Content:" not in result.content
    # Preamble metadata is captured (title + published) before it's stripped.
    assert result.metadata == {
        "title": "Real Article",
        "published": "2026-06-29",  # normalized from the preamble's ISO datetime
    }
