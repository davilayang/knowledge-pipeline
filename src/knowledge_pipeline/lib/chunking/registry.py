# Chunking strategy registry — maps strategy names from strategies.yaml
# to functions that split text into Chunks.

from __future__ import annotations

from collections.abc import Callable

from langchain_text_splitters import (
    CharacterTextSplitter,
    MarkdownHeaderTextSplitter,
    MarkdownTextSplitter,
    RecursiveCharacterTextSplitter,
    SentenceTransformersTokenTextSplitter,
    TokenTextSplitter,
)

from .types import Chunk

# Each chunking function takes text and returns list[Chunk].
ChunkingFn = Callable[[str], list[Chunk]]

# Rough char-per-token ratio for character-based splitters.
_CHARS_PER_TOKEN = 4


def _to_chunks(docs: list, heading_keys: tuple[str, ...] = ()) -> list[Chunk]:
    """Convert langchain Documents to Chunks."""
    chunks = []
    for i, doc in enumerate(docs):
        if heading_keys:
            parts = [doc.metadata.get(k, "") for k in heading_keys if doc.metadata.get(k)]
            heading = " > ".join(parts)
        else:
            heading = ""
        chunks.append(Chunk(text=doc.page_content, heading=heading, index=i))
    return chunks


# ---------------------------------------------------------------------------
# Chunking strategies
#
# Each factory takes (chunk_size, chunk_overlap) in tokens and returns a
# ChunkingFn. Splitter instances are created once per factory call and
# reused across all chunks in that strategy — but not cached across
# separate factory calls. This is acceptable because each strategy calls
# the factory once at module load time.
# ---------------------------------------------------------------------------


def _make_markdown(chunk_size: int, chunk_overlap: int) -> ChunkingFn:
    """Split by markdown headers, then recursively split large sections."""
    header_splitter = MarkdownHeaderTextSplitter(
        headers_to_split_on=[("#", "h1"), ("##", "h2"), ("###", "h3")],
        strip_headers=False,
    )
    size_splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size * _CHARS_PER_TOKEN,
        chunk_overlap=chunk_overlap * _CHARS_PER_TOKEN,
    )

    def _chunk(text: str) -> list[Chunk]:
        header_docs = header_splitter.split_text(text)
        split_docs = size_splitter.split_documents(header_docs)
        return _to_chunks(split_docs, heading_keys=("h1", "h2", "h3"))

    return _chunk


def _make_markdown_syntax(chunk_size: int, chunk_overlap: int) -> ChunkingFn:
    """Markdown-aware recursive splitting that respects code blocks, lists, and headers."""
    splitter = MarkdownTextSplitter(
        chunk_size=chunk_size * _CHARS_PER_TOKEN,
        chunk_overlap=chunk_overlap * _CHARS_PER_TOKEN,
    )

    def _chunk(text: str) -> list[Chunk]:
        return _to_chunks(splitter.create_documents([text]))

    return _chunk


def _make_recursive(chunk_size: int, chunk_overlap: int) -> ChunkingFn:
    """Recursive character splitting — tries large separators first, then smaller."""
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size * _CHARS_PER_TOKEN,
        chunk_overlap=chunk_overlap * _CHARS_PER_TOKEN,
    )

    def _chunk(text: str) -> list[Chunk]:
        return _to_chunks(splitter.create_documents([text]))

    return _chunk


def _make_fixed(chunk_size: int, chunk_overlap: int) -> ChunkingFn:
    """Fixed-size character windows with overlap. Control experiment."""
    splitter = CharacterTextSplitter(
        separator="",
        chunk_size=chunk_size * _CHARS_PER_TOKEN,
        chunk_overlap=chunk_overlap * _CHARS_PER_TOKEN,
    )

    def _chunk(text: str) -> list[Chunk]:
        return _to_chunks(splitter.create_documents([text]))

    return _chunk


def _make_token(chunk_size: int, chunk_overlap: int) -> ChunkingFn:
    """Token-count-based splitting using tiktoken."""
    splitter = TokenTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
    )

    def _chunk(text: str) -> list[Chunk]:
        return _to_chunks(splitter.create_documents([text]))

    return _chunk


def _make_sentence_transformer_token(chunk_size: int, chunk_overlap: int) -> ChunkingFn:
    """Token splitting aligned to the embedding model's tokenizer."""
    splitter = SentenceTransformersTokenTextSplitter(
        tokens_per_chunk=chunk_size,
        chunk_overlap=chunk_overlap,
    )

    def _chunk(text: str) -> list[Chunk]:
        return _to_chunks(splitter.create_documents([text]))

    return _chunk


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

# Maps strategy name to a factory: (chunk_size, chunk_overlap) -> ChunkingFn
_CHUNKING_FACTORIES: dict[str, Callable[[int, int], ChunkingFn]] = {
    "markdown": _make_markdown,
    "markdown_syntax": _make_markdown_syntax,
    "recursive": _make_recursive,
    "fixed": _make_fixed,
    "token": _make_token,
    "sentence_transformer_token": _make_sentence_transformer_token,
}

_DEFAULT_CHUNK_SIZE = 800
_DEFAULT_OVERLAP = 100


def get_chunking_fn(
    name: str,
    chunk_size: int = _DEFAULT_CHUNK_SIZE,
    chunk_overlap: int = _DEFAULT_OVERLAP,
) -> ChunkingFn:
    """Build a chunking function by name with the given size parameters."""
    factory = _CHUNKING_FACTORIES.get(name)
    if factory is None:
        available = ", ".join(sorted(_CHUNKING_FACTORIES))
        raise ValueError(f"Unknown chunking strategy: {name!r}. Available: {available}")
    return factory(chunk_size, chunk_overlap)
