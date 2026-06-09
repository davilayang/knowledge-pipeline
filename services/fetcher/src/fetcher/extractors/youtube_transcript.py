"""YouTube transcript chunks to markdown."""

from typing import Any


_PARAGRAPH_GAP_S = 5.0


def chunks_to_markdown(chunks: list[dict[str, Any]]) -> str:
    """Format transcript chunks into markdown-ish paragraphs."""
    if not chunks:
        return ""

    parts: list[str] = []
    previous_end = 0.0
    for chunk in chunks:
        text = (chunk.get("text") or "").strip()
        if not text:
            continue
        start = float(chunk.get("start") or 0.0)
        if parts and (start - previous_end) > _PARAGRAPH_GAP_S:
            parts.append("\n\n")
        parts.append(text + " ")
        previous_end = start + float(chunk.get("duration") or 0.0)

    return "".join(parts).strip()
