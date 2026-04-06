import pytest

from knowledge_pipeline.lib.chunking import Chunk, get_chunking_fn

# Sample text for testing — enough content to produce multiple chunks at small sizes.
SAMPLE_MD = """# Introduction

This is the first section with introductory content about the topic.
It has multiple sentences to provide enough text for chunking.

## Details

Here are the details of the implementation. This section covers
the technical aspects and provides code examples.

```python
def hello():
    print("world")
```

## Conclusion

Final thoughts and summary of the key points discussed above.
This wraps up the document with closing remarks.
"""

SHORT_TEXT = "This is a short paragraph with enough content to not be empty."


# ---------------------------------------------------------------------------
# Registry tests
# ---------------------------------------------------------------------------


def test_get_chunking_fn_unknown():
    """Unknown strategy name should raise ValueError."""
    with pytest.raises(ValueError, match="Unknown chunking strategy"):
        get_chunking_fn("nonexistent")


# ---------------------------------------------------------------------------
# Per-strategy tests
# ---------------------------------------------------------------------------

STRATEGIES = ["markdown", "markdown_syntax", "recursive", "fixed", "token"]


@pytest.mark.parametrize("strategy", STRATEGIES)
def test_returns_chunk_type(strategy):
    """All strategies should return list[Chunk]."""
    fn = get_chunking_fn(strategy, chunk_size=200, chunk_overlap=20)
    chunks = fn(SAMPLE_MD)
    assert len(chunks) > 0
    assert all(isinstance(c, Chunk) for c in chunks)


@pytest.mark.parametrize("strategy", STRATEGIES)
def test_indices_sequential(strategy):
    """Chunk indices should be sequential starting from 0."""
    fn = get_chunking_fn(strategy, chunk_size=200, chunk_overlap=20)
    chunks = fn(SAMPLE_MD)
    for i, chunk in enumerate(chunks):
        assert chunk.index == i


@pytest.mark.parametrize("strategy", STRATEGIES)
def test_short_text_single_chunk(strategy):
    """Short text should produce a single chunk."""
    fn = get_chunking_fn(strategy, chunk_size=800, chunk_overlap=100)
    chunks = fn(SHORT_TEXT)
    assert len(chunks) == 1
    assert chunks[0].index == 0


@pytest.mark.parametrize("strategy", STRATEGIES)
def test_empty_text(strategy):
    """Empty text should produce no chunks."""
    fn = get_chunking_fn(strategy, chunk_size=800, chunk_overlap=100)
    assert fn("") == []


def test_markdown_preserves_headings():
    """Markdown strategy should preserve heading metadata."""
    fn = get_chunking_fn("markdown", chunk_size=800, chunk_overlap=100)
    chunks = fn(SAMPLE_MD)
    headings = [c.heading for c in chunks]
    assert any("Introduction" in h for h in headings)
    assert any("Details" in h for h in headings)


def test_smaller_chunk_size_produces_more_chunks():
    """Smaller chunk_size should produce more chunks."""
    fn_large = get_chunking_fn("recursive", chunk_size=800, chunk_overlap=50)
    fn_small = get_chunking_fn("recursive", chunk_size=100, chunk_overlap=10)
    large_chunks = fn_large(SAMPLE_MD)
    small_chunks = fn_small(SAMPLE_MD)
    assert len(small_chunks) > len(large_chunks)
