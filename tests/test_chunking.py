from knowledge_pipeline.lib.chunking import Chunk, chunk_markdown


def test_chunk_short_text():
    """Short text should produce a single chunk."""
    text = "This is a short paragraph with enough content to not be empty."
    chunks = chunk_markdown(text)
    assert len(chunks) == 1
    assert chunks[0].index == 0
    assert chunks[0].heading == ""


def test_chunk_respects_headings():
    """Chunks should preserve heading context."""
    text = "# Introduction\n\nFirst section content here.\n\n# Details\n\nSecond section content."
    chunks = chunk_markdown(text)
    headings = [c.heading for c in chunks]
    assert "# Introduction" in headings
    assert "# Details" in headings


def test_chunk_indices_sequential():
    """Chunk indices should be sequential starting from 0."""
    text = "\n\n".join([f"Paragraph {i} with enough text to matter." for i in range(50)])
    chunks = chunk_markdown(text, chunk_tokens=50)
    for i, chunk in enumerate(chunks):
        assert chunk.index == i


def test_chunk_empty_text():
    """Empty text should produce no chunks."""
    assert chunk_markdown("") == []
    assert chunk_markdown("   ") == []


def test_chunk_returns_chunk_type():
    """Chunks should be Chunk dataclass instances."""
    chunks = chunk_markdown("Some content that is long enough to be valid.")
    assert all(isinstance(c, Chunk) for c in chunks)
