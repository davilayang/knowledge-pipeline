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

# Token/char estimation
_CHARS_PER_TOKEN = 4
_DEFAULT_CHUNK_SIZE = 800 * _CHARS_PER_TOKEN  # 3200 chars
_DEFAULT_OVERLAP = 100 * _CHARS_PER_TOKEN  # 400 chars


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
# ---------------------------------------------------------------------------


def _markdown_chunking(text: str) -> list[Chunk]:
    """Split by markdown headers, then recursively split large sections.

    Two-stage: MarkdownHeaderTextSplitter preserves heading metadata,
    then RecursiveCharacterTextSplitter enforces size limits. Headings
    are kept in the chunk text and tracked as metadata.
    """
    header_splitter = MarkdownHeaderTextSplitter(
        headers_to_split_on=[("#", "h1"), ("##", "h2"), ("###", "h3")],
        strip_headers=False,
    )
    size_splitter = RecursiveCharacterTextSplitter(
        chunk_size=_DEFAULT_CHUNK_SIZE,
        chunk_overlap=_DEFAULT_OVERLAP,
    )
    header_docs = header_splitter.split_text(text)
    split_docs = size_splitter.split_documents(header_docs)
    return _to_chunks(split_docs, heading_keys=("h1", "h2", "h3"))


def _markdown_syntax_chunking(text: str) -> list[Chunk]:
    """Markdown-aware recursive splitting that respects code blocks, lists, and headers.

    Uses MarkdownTextSplitter which extends RecursiveCharacterTextSplitter
    with markdown-specific separators (```, ##, ---, etc.) so code blocks
    and list items are not broken mid-element.
    """
    splitter = MarkdownTextSplitter(
        chunk_size=_DEFAULT_CHUNK_SIZE,
        chunk_overlap=_DEFAULT_OVERLAP,
    )
    docs = splitter.create_documents([text])
    return _to_chunks(docs)


def _recursive_chunking(text: str) -> list[Chunk]:
    """Recursive character splitting — tries large separators first, then smaller.

    Attempts to split on paragraphs (\\n\\n), then newlines (\\n),
    then spaces, then characters. Keeps semantically coherent units
    as large as possible within the size limit.
    """
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=_DEFAULT_CHUNK_SIZE,
        chunk_overlap=_DEFAULT_OVERLAP,
    )
    docs = splitter.create_documents([text])
    return _to_chunks(docs)


def _fixed_chunking(text: str) -> list[Chunk]:
    """Fixed-size character windows with overlap.

    Splits at exact character boundaries regardless of content structure.
    Control experiment — tests the floor performance of naive chunking.
    """
    splitter = CharacterTextSplitter(
        separator="",
        chunk_size=512 * _CHARS_PER_TOKEN,
        chunk_overlap=50 * _CHARS_PER_TOKEN,
    )
    docs = splitter.create_documents([text])
    return _to_chunks(docs)


def _token_chunking(text: str) -> list[Chunk]:
    """Token-count-based splitting using tiktoken.

    Splits by actual token count rather than character estimation.
    More accurate chunk sizes for LLM context windows.
    """
    splitter = TokenTextSplitter(
        chunk_size=800,
        chunk_overlap=100,
    )
    docs = splitter.create_documents([text])
    return _to_chunks(docs)


def _sentence_transformer_token_chunking(text: str) -> list[Chunk]:
    """Token splitting aligned to the embedding model's tokenizer.

    Uses sentence-transformers tokenizer so chunk boundaries match
    exactly what the embedding model sees. Avoids mid-token splits
    that degrade embedding quality.
    """
    splitter = SentenceTransformersTokenTextSplitter(
        chunk_overlap=50,
        tokens_per_chunk=256,  # MiniLM-L6-v2 max context
    )
    docs = splitter.create_documents([text])
    return _to_chunks(docs)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_CHUNKING_REGISTRY: dict[str, ChunkingFn] = {
    "markdown": _markdown_chunking,
    "markdown_syntax": _markdown_syntax_chunking,
    "recursive": _recursive_chunking,
    "fixed": _fixed_chunking,
    "token": _token_chunking,
    "sentence_transformer_token": _sentence_transformer_token_chunking,
}


def get_chunking_fn(name: str) -> ChunkingFn:
    """Look up a chunking function by name from strategies.yaml."""
    fn = _CHUNKING_REGISTRY.get(name)
    if fn is None:
        available = ", ".join(sorted(_CHUNKING_REGISTRY))
        raise ValueError(f"Unknown chunking strategy: {name!r}. Available: {available}")
    return fn
