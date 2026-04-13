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


def _make_semantic(chunk_size: int, chunk_overlap: int) -> ChunkingFn:
    """Semantic chunking — split where cosine similarity between consecutive sentences drops.

    How it works::

        Input text
            │
            ▼
        1. Split into sentences (regex on . ? !)
            │
            "ML is a subset of AI."          s1 = [0.12, -0.03, ...]
            "It builds systems that learn."   s2 = [0.11, -0.02, ...]  ← similar to s1
            "Deep learning uses NNs."         s3 = [0.45,  0.21, ...]  ← different from s2
            "CNNs handle images."             s4 = [0.43,  0.19, ...]  ← similar to s3
            │
            ▼
        2. Embed each sentence with MiniLM → 384-dim vector
            │
            ▼
        3. Cosine similarity between consecutive pairs
            │
            sim(s1, s2) = 0.95  ← same topic
            sim(s2, s3) = 0.41  ← topic shift!
            sim(s3, s4) = 0.92  ← same topic
            │
            ▼
        4. Split where similarity < 80th percentile of all pairs
            │
            s1, s2 | SPLIT | s3, s4
            │
            ▼
        5. Chunks: ["ML is... It builds...", "Deep learning... CNNs..."]
            │
            ▼
        6. Fallback: any chunk > chunk_size → RecursiveCharacterTextSplitter

    The percentile threshold (80) means: split at pairs in the bottom 20%
    of similarity. Higher threshold = fewer, larger chunks.

    Note: the embedding model for sentence similarity (MiniLM) is hardcoded
    and independent of the index strategy's embedding model.
    """
    import warnings

    from langchain_experimental.text_splitter import SemanticChunker

    # HuggingFaceEmbeddings is deprecated in favour of langchain-huggingface,
    # but that package doesn't support Python 3.13 yet.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        from langchain_community.embeddings import HuggingFaceEmbeddings

        embeddings = HuggingFaceEmbeddings(model_name="all-MiniLM-L6-v2")
    semantic_splitter = SemanticChunker(
        embeddings=embeddings,
        breakpoint_threshold_type="percentile",
        breakpoint_threshold_amount=80,
    )
    # Fallback splitter for oversized semantic chunks
    size_splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size * _CHARS_PER_TOKEN,
        chunk_overlap=chunk_overlap * _CHARS_PER_TOKEN,
    )

    def _chunk(text: str) -> list[Chunk]:
        semantic_docs = semantic_splitter.create_documents([text])
        # Split any oversized chunks
        final_docs = size_splitter.split_documents(semantic_docs)
        return _to_chunks(final_docs)

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
    "semantic": _make_semantic,
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
