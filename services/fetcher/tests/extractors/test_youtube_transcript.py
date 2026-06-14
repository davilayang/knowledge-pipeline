"""Tests for transcript chunk formatting."""

from fetcher.extractors.youtube_transcript import chunks_to_markdown


def test_formats_simple_chunks() -> None:
    """Within-paragraph chunks (gap ≤ _PARAGRAPH_GAP_S=5s) join with a single
    space — not concatenated, not on separate lines. Pins the actual format
    so a regression that loses the space separator would be caught."""
    chunks = [
        {"text": "Hello world", "start": 0.0, "duration": 2.0},
        {"text": "Next sentence", "start": 2.5, "duration": 1.5},
    ]
    assert chunks_to_markdown(chunks) == "Hello world Next sentence"


def test_empty_chunks_returns_empty_string() -> None:
    assert chunks_to_markdown([]) == ""


def test_chunks_paragraph_break_on_long_pause() -> None:
    chunks = [
        {"text": "First half", "start": 0.0, "duration": 1.0},
        {"text": "After a long pause", "start": 30.0, "duration": 2.0},
    ]
    assert "\n\n" in chunks_to_markdown(chunks)
