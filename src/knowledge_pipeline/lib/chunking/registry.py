# Chunking strategy registry — maps strategy names from strategies.yaml
# to functions that split text into Chunks.

from __future__ import annotations

from collections.abc import Callable

from langchain_text_splitters import (
    MarkdownHeaderTextSplitter,
    RecursiveCharacterTextSplitter,
)

from .types import Chunk

# Each chunking function takes text and returns list[Chunk].
ChunkingFn = Callable[[str], list[Chunk]]

# Token/char estimation
_CHARS_PER_TOKEN = 4
_DEFAULT_CHUNK_SIZE = 800 * _CHARS_PER_TOKEN  # 3200 chars
_DEFAULT_OVERLAP = 100 * _CHARS_PER_TOKEN  # 400 chars


def _markdown_chunking(text: str) -> list[Chunk]:
    """Split by markdown headers, then recursively split large sections."""
    headers_to_split_on = [
        ("#", "h1"),
        ("##", "h2"),
        ("###", "h3"),
    ]
    header_splitter = MarkdownHeaderTextSplitter(
        headers_to_split_on=headers_to_split_on,
        strip_headers=False,
    )
    recursive_splitter = RecursiveCharacterTextSplitter(
        chunk_size=_DEFAULT_CHUNK_SIZE,
        chunk_overlap=_DEFAULT_OVERLAP,
    )

    header_docs = header_splitter.split_text(text)
    split_docs = recursive_splitter.split_documents(header_docs)

    chunks = []
    for i, doc in enumerate(split_docs):
        heading_parts = [doc.metadata.get(k, "") for k in ("h1", "h2", "h3") if doc.metadata.get(k)]
        heading = " > ".join(heading_parts)
        chunks.append(Chunk(text=doc.page_content, heading=heading, index=i))

    return chunks


def _recursive_chunking(text: str) -> list[Chunk]:
    """Recursive character splitting — tries large splits first, then smaller."""
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=_DEFAULT_CHUNK_SIZE,
        chunk_overlap=_DEFAULT_OVERLAP,
    )
    docs = splitter.create_documents([text])
    return [Chunk(text=doc.page_content, heading="", index=i) for i, doc in enumerate(docs)]


def _fixed_chunking(text: str) -> list[Chunk]:
    """Fixed-size character windows with overlap."""
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=512 * _CHARS_PER_TOKEN,
        chunk_overlap=50 * _CHARS_PER_TOKEN,
        separators=[""],  # force character-level splitting
    )
    docs = splitter.create_documents([text])
    return [Chunk(text=doc.page_content, heading="", index=i) for i, doc in enumerate(docs)]


# Registry mapping chunking strategy names to functions.
_CHUNKING_REGISTRY: dict[str, ChunkingFn] = {
    "markdown": _markdown_chunking,
    "recursive": _recursive_chunking,
    "fixed": _fixed_chunking,
}


def get_chunking_fn(name: str) -> ChunkingFn:
    """Look up a chunking function by name from strategies.yaml."""
    fn = _CHUNKING_REGISTRY.get(name)
    if fn is None:
        available = ", ".join(sorted(_CHUNKING_REGISTRY))
        raise ValueError(f"Unknown chunking strategy: {name!r}. Available: {available}")
    return fn
