"""PDF bytes to markdown via pymupdf4llm."""

import io
import logging

import pymupdf4llm


logger = logging.getLogger(__name__)


def to_markdown(pdf_bytes: bytes) -> str:
    """Render PDF bytes to markdown. Empty string on failure."""
    if not pdf_bytes:
        return ""
    try:
        import pymupdf

        document = pymupdf.open(stream=io.BytesIO(pdf_bytes), filetype="pdf")
        try:
            return pymupdf4llm.to_markdown(document) or ""
        finally:
            document.close()
    except Exception as exc:
        logger.warning("pymupdf4llm extraction failed: %s", exc)
        return ""
