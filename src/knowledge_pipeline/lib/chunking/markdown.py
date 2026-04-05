# Markdown-aware chunking for knowledge indexing.
#
# Ported from newsletter-assistant's pipeline.chunking module.
# Respects heading boundaries, groups paragraphs, and provides overlap.

from __future__ import annotations

import re

from .types import Chunk

_CHARS_PER_TOKEN = 4
DEFAULT_CHUNK_TOKENS = 800
DEFAULT_OVERLAP_TOKENS = 100


class MarkdownChunking:
    """Markdown-aware chunking strategy.

    Respects heading boundaries, groups paragraphs, and provides overlap.
    Implements the ChunkingStrategy protocol.
    """

    def __init__(
        self,
        chunk_tokens: int = DEFAULT_CHUNK_TOKENS,
        overlap_tokens: int = DEFAULT_OVERLAP_TOKENS,
    ) -> None:
        self._chunk_tokens = chunk_tokens
        self._overlap_tokens = overlap_tokens

    @property
    def name(self) -> str:
        return "markdown"

    def chunk(self, text: str) -> list[Chunk]:
        return chunk_markdown(text, self._chunk_tokens, self._overlap_tokens)


# TODO: Using langchain functions for simpler implementations
def chunk_markdown(
    text: str,
    chunk_tokens: int = DEFAULT_CHUNK_TOKENS,
    overlap_tokens: int = DEFAULT_OVERLAP_TOKENS,
) -> list[Chunk]:
    """Split markdown into semantically coherent, overlapping chunks."""
    chunk_chars = chunk_tokens * _CHARS_PER_TOKEN
    overlap_chars = overlap_tokens * _CHARS_PER_TOKEN

    sections = _split_into_sections(text)
    chunks: list[Chunk] = []

    for heading, body in sections:
        if not body.strip():
            continue
        paragraphs = _split_paragraphs(body)
        section_chunks = _pack_paragraphs(paragraphs, chunk_chars, overlap_chars, heading)
        chunks.extend(section_chunks)

    for i, chunk in enumerate(chunks):
        chunk.index = i

    return chunks


_HEADING_RE = re.compile(r"^(#{1,3})\s+(.+)$", re.MULTILINE)


def _split_into_sections(text: str) -> list[tuple[str, str]]:
    matches = list(_HEADING_RE.finditer(text))
    if not matches:
        return [("", text)]

    sections: list[tuple[str, str]] = []
    if matches[0].start() > 0:
        preamble = text[: matches[0].start()]
        if preamble.strip():
            sections.append(("", preamble))

    for i, match in enumerate(matches):
        heading = match.group(0).strip()
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        sections.append((heading, text[start:end]))

    return sections


def _split_paragraphs(text: str) -> list[str]:
    raw = re.split(r"\n{2,}", text.strip())
    return [p.strip() for p in raw if p.strip()]


def _pack_paragraphs(
    paragraphs: list[str],
    chunk_chars: int,
    overlap_chars: int,
    heading: str,
) -> list[Chunk]:
    if not paragraphs:
        return []

    heading_prefix = f"{heading}\n\n" if heading else ""
    heading_len = len(heading_prefix)
    effective_limit = chunk_chars - heading_len

    chunks: list[Chunk] = []
    current_parts: list[str] = []
    current_len = 0

    for para in paragraphs:
        para_len = len(para)

        if para_len > effective_limit:
            if current_parts:
                chunk_text = heading_prefix + "\n\n".join(current_parts)
                chunks.append(Chunk(text=chunk_text, heading=heading, index=0))
                current_parts = []
                current_len = 0
            hard_chunks = _hard_split(para, effective_limit, overlap_chars)
            for hc in hard_chunks:
                chunks.append(Chunk(text=heading_prefix + hc, heading=heading, index=0))
            continue

        separator_len = 2 if current_parts else 0
        if current_len + separator_len + para_len > effective_limit and current_parts:
            chunk_text = heading_prefix + "\n\n".join(current_parts)
            chunks.append(Chunk(text=chunk_text, heading=heading, index=0))
            overlap_part = _get_overlap(current_parts, overlap_chars)
            current_parts = [overlap_part] if overlap_part else []
            current_len = len(overlap_part) if overlap_part else 0

        current_parts.append(para)
        current_len += (separator_len if current_len > 0 else 0) + para_len

    if current_parts:
        chunk_text = heading_prefix + "\n\n".join(current_parts)
        chunks.append(Chunk(text=chunk_text, heading=heading, index=0))

    return chunks


def _get_overlap(parts: list[str], overlap_chars: int) -> str:
    if not parts or overlap_chars <= 0:
        return ""
    collected: list[str] = []
    total = 0
    for part in reversed(parts):
        if total + len(part) > overlap_chars and collected:
            break
        collected.append(part)
        total += len(part)
    collected.reverse()
    result = "\n\n".join(collected)
    if len(result) > overlap_chars:
        result = result[-overlap_chars:]
    return result


def _hard_split(text: str, chunk_chars: int, overlap_chars: int) -> list[str]:
    sentences = re.split(r"(?<=[.!?])\s+", text)
    if len(sentences) <= 1:
        char_chunks: list[str] = []
        start = 0
        while start < len(text):
            end = start + chunk_chars
            char_chunks.append(text[start:end])
            if end >= len(text):
                break
            start = end - overlap_chars
        return char_chunks

    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for sentence in sentences:
        sep_len = 1 if current else 0
        if current_len + sep_len + len(sentence) > chunk_chars and current:
            chunks.append(" ".join(current))
            overlap: list[str] = []
            olen = 0
            for s in reversed(current):
                if olen + len(s) > overlap_chars and overlap:
                    break
                overlap.append(s)
                olen += len(s)
            overlap.reverse()
            current = overlap
            current_len = sum(len(s) for s in current) + max(0, len(current) - 1)
        current.append(sentence)
        current_len += sep_len + len(sentence)
    if current:
        chunks.append(" ".join(current))
    return chunks
