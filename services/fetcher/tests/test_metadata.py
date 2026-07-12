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


def test_build_metadata_normalizes_published_to_plain_date():
    # The consumer parses `published` with date.fromisoformat, which rejects a
    # time component — normalize any ISO datetime down to YYYY-MM-DD at the source.
    assert build_metadata(published="2026-06-29T00:00:00Z") == {"published": "2026-06-29"}
    assert build_metadata(published="2026-06-29T10:30:00+00:00") == {"published": "2026-06-29"}


def test_build_metadata_drops_unparseable_published():
    # A date the source can't parse to ISO is worse than none — it would crash the
    # consumer's date.fromisoformat. Drop it rather than emit a landmine.
    assert build_metadata(title="T", published="March 1, 2026") == {"title": "T"}


def test_build_metadata_normalizes_us_slash_dates():
    # Jina returns some sources' Published Time as US MM/DD/YYYY (e.g. humanlayer);
    # coerce to ISO rather than drop a real date. Single-digit month/day allowed.
    assert build_metadata(published="11/25/2025") == {"published": "2025-11-25"}
    assert build_metadata(published="3/12/2026") == {"published": "2026-03-12"}
