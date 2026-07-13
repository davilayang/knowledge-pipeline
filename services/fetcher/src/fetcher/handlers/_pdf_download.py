"""Shared PDF byte-download for the pdf + arxiv handlers.

Streams a PDF URL into memory with a hard size cap so a pathologically large
file can't exhaust the process. Used by both handlers' pymupdf tiers, which
need raw bytes (the LlamaParse tiers pass the URL and render server-side).
"""

import httpx

MAX_PDF_BYTES = 50_000_000


class PdfTooLarge(Exception):
    """Raised when a streamed PDF exceeds the byte cap before completing."""


async def download_pdf_bytes(
    client: httpx.AsyncClient,
    url: str,
    *,
    timeout: int,
    max_bytes: int = MAX_PDF_BYTES,
) -> tuple[bytes, int]:
    """Stream `url` into bytes, aborting past `max_bytes`. Returns (bytes, status).

    Raises `PdfTooLarge` when the cap is breached mid-stream; httpx transport
    errors propagate to the caller to handle.
    """
    async with client.stream("GET", url, follow_redirects=True, timeout=timeout) as response:
        status = response.status_code
        chunks: list[bytes] = []
        total = 0
        async for chunk in response.aiter_bytes(chunk_size=64 * 1024):
            total += len(chunk)
            if total > max_bytes:
                raise PdfTooLarge(f"pdf at {url} exceeds {max_bytes}-byte cap")
            chunks.append(chunk)
    return b"".join(chunks), status
