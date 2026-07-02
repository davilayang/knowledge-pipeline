from datetime import date
from pathlib import Path

from domains.wiki.io import read_page, write_page
from domains.wiki.types import WikiPage


def _make_page(**overrides) -> WikiPage:
    defaults = {
        "entity_id": "concept__rag",
        "title": "Retrieval-Augmented Generation",
        "page_type": "concept",
        "summary": "RAG augments LLM generation with retrieval over a corpus.",
        "related": ["concept__vector_db"],
        "sources": ["content_123"],
        "updated_at": date(2026, 4, 21),
        "content": "# RAG\n\nRAG is a technique for augmenting LLM generation.",
    }
    defaults.update(overrides)
    return WikiPage(**defaults)


def _write(path: Path, page: WikiPage, **overrides) -> None:
    """Default writer kwargs for round-trip / overwrite tests."""
    write_page(
        path,
        page,
        aliases=overrides.get("aliases", ["Retrieval-Augmented Generation"]),
        num_sources=overrides.get("num_sources", 1),
        sources=overrides.get("sources", page.sources),
        related=overrides.get("related", page.related),
    )


def test_write_then_read_roundtrip(tmp_path: Path):
    page = _make_page()
    path = tmp_path / "concept" / "rag.md"

    _write(path, page)
    loaded = read_page(path)

    assert loaded.entity_id == page.entity_id
    assert loaded.title == page.title
    assert loaded.page_type == page.page_type
    assert loaded.summary == page.summary
    assert loaded.related == page.related
    assert loaded.sources == page.sources
    assert loaded.updated_at == page.updated_at
    assert loaded.content == page.content


def test_sources_frontmatter_is_producer_authoritative(tmp_path: Path):
    """`sources` rendered to frontmatter comes from the passed-in producer list
    (the accumulated source ids for the entity), NOT page.sources (the per-item
    [source_id] the LLM emits). Mirrors how aliases / num_sources are already
    producer-authoritative."""
    page = _make_page(sources=["content_123"])  # the volatile per-item value
    path = tmp_path / "rag.md"

    write_page(
        path,
        page,
        aliases=[],
        num_sources=2,
        sources=["content_a", "content_b"],
        related=page.related,
    )

    assert read_page(path).sources == ["content_a", "content_b"]


def test_write_creates_parent_dirs(tmp_path: Path):
    page = _make_page()
    path = tmp_path / "nested" / "deep" / "page.md"

    _write(path, page)

    assert path.exists()


def test_atomic_write_no_tmp_left(tmp_path: Path):
    page = _make_page()
    path = tmp_path / "page.md"

    _write(path, page)

    assert path.exists()
    assert not path.with_suffix(".json.tmp").exists()
    assert not path.with_suffix(".tmp").exists()


def test_write_overwrites_existing(tmp_path: Path):
    path = tmp_path / "page.md"
    page_v1 = _make_page(content="# V1")
    page_v2 = _make_page(content="# V2", sources=["content_123", "content_456"])

    _write(path, page_v1)
    _write(path, page_v2, num_sources=2)

    loaded = read_page(path)
    assert loaded.content == "# V2"
    assert loaded.sources == ["content_123", "content_456"]


def test_read_preserves_multiline_content(tmp_path: Path):
    content = "# RAG\n\n## How It Works\n\n1. Index\n2. Retrieve\n3. Generate"
    page = _make_page(content=content)
    path = tmp_path / "page.md"

    _write(path, page)
    loaded = read_page(path)

    assert loaded.content == content


def test_read_empty_lists(tmp_path: Path):
    page = _make_page(related=[], sources=[])
    path = tmp_path / "page.md"

    write_page(path, page, aliases=[], num_sources=0, sources=[], related=[])
    loaded = read_page(path)

    assert loaded.related == []
    assert loaded.sources == []


def test_write_page_emits_new_fields_in_stable_order(tmp_path: Path):
    """Frontmatter field order is part of the bridge contract."""
    page = _make_page()
    path = tmp_path / "page.md"

    write_page(
        path,
        page,
        aliases=["RAG", "Retrieval-Augmented Generation"],
        num_sources=3,
        sources=page.sources,
        related=page.related,
    )

    text = path.read_text(encoding="utf-8")
    body = text.split("---\n", 2)[1]
    keys = [
        line.split(":", 1)[0]
        for line in body.strip().splitlines()
        if line and not line.startswith(" ")
    ]
    assert keys == [
        "entity_id",
        "title",
        "page_type",
        "summary",
        "aliases",
        "related",
        "sources",
        "num_sources",
        "updated_at",
    ]


def test_write_page_serializes_aliases_as_inline_list(tmp_path: Path):
    """aliases / related / sources are emitted inline (`[a, b]`), not block."""
    page = _make_page()
    path = tmp_path / "page.md"

    write_page(
        path,
        page,
        aliases=["RAG", "Retrieval-Augmented Generation"],
        num_sources=1,
        sources=page.sources,
        related=page.related,
    )

    text = path.read_text(encoding="utf-8")
    assert "aliases: [RAG, Retrieval-Augmented Generation]" in text
    # Sanity: not the multi-line block form
    assert "aliases:\n- RAG" not in text


def test_write_page_emits_num_sources_as_int(tmp_path: Path):
    page = _make_page()
    path = tmp_path / "page.md"

    write_page(path, page, aliases=[], num_sources=5, sources=page.sources, related=page.related)

    text = path.read_text(encoding="utf-8")
    assert "num_sources: 5" in text
