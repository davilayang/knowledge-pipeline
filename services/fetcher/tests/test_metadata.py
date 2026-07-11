"""Tests for the canonical source-metadata contract."""

from fetcher.metadata import build_metadata


def test_build_metadata_emits_canonical_keys():
    # The builder owns the contract key names the consumer reads (title / authors
    # / published / arxiv_id) — handlers go through it so a `author`-vs-`authors`
    # drift can't slip into a hand-written dict literal.
    md = build_metadata(
        title="A Title", authors="Jane Doe", published="2026-03-01", arxiv_id="2401.001"
    )
    assert md == {
        "title": "A Title",
        "authors": "Jane Doe",
        "published": "2026-03-01",
        "arxiv_id": "2401.001",
    }


def test_build_metadata_omits_absent_fields():
    # A field with no value is a MISSING key, never a null — so a tier that can't
    # find a publish date leaves `published` absent (no fake value downstream).
    assert build_metadata(title="T", authors=None, published="") == {"title": "T"}
