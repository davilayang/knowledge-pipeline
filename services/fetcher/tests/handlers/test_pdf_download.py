"""Tests for the shared PDF byte-download helper (pdf + arxiv handlers)."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from fetcher.handlers._pdf_download import PdfTooLarge, download_pdf_bytes


def _stream_ctxmgr(status_code: int, chunks: list[bytes]):
    """Mock of httpx.AsyncClient.stream(...) returning an async byte iterator."""
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


@pytest.mark.asyncio
async def test_download_pdf_bytes_accumulates_chunks_and_returns_status() -> None:
    client = MagicMock()
    client.stream = MagicMock(return_value=_stream_ctxmgr(200, [b"%PDF ", b"rest"]))

    data, status = await download_pdf_bytes(client, "https://x/p.pdf", timeout=30)

    assert data == b"%PDF rest"
    assert status == 200


@pytest.mark.asyncio
async def test_download_pdf_bytes_raises_when_over_cap() -> None:
    client = MagicMock()
    client.stream = MagicMock(return_value=_stream_ctxmgr(200, [b"x" * 10, b"y" * 10]))

    with pytest.raises(PdfTooLarge):
        await download_pdf_bytes(client, "https://x/big.pdf", timeout=30, max_bytes=15)
