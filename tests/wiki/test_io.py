from datetime import date
from pathlib import Path

from knowledge_pipeline.lib.wiki.io import read_page, write_page
from knowledge_pipeline.lib.wiki.types import WikiPage


def _make_page(**overrides) -> WikiPage:
    defaults = {
        "entity_id": "concept__rag",
        "title": "Retrieval-Augmented Generation",
        "page_type": "concept",
        "related": ["concept__vector_db"],
        "sources": ["content_123"],
        "updated_at": date(2026, 4, 21),
        "content": "# RAG\n\nRAG is a technique for augmenting LLM generation.",
    }
    defaults.update(overrides)
    return WikiPage(**defaults)


def test_write_then_read_roundtrip(tmp_path: Path):
    page = _make_page()
    path = tmp_path / "concept" / "rag.md"

    write_page(path, page)
    loaded = read_page(path)

    assert loaded.entity_id == page.entity_id
    assert loaded.title == page.title
    assert loaded.page_type == page.page_type
    assert loaded.related == page.related
    assert loaded.sources == page.sources
    assert loaded.updated_at == page.updated_at
    assert loaded.content == page.content


def test_write_creates_parent_dirs(tmp_path: Path):
    page = _make_page()
    path = tmp_path / "nested" / "deep" / "page.md"

    write_page(path, page)

    assert path.exists()


def test_atomic_write_no_tmp_left(tmp_path: Path):
    page = _make_page()
    path = tmp_path / "page.md"

    write_page(path, page)

    assert path.exists()
    assert not path.with_suffix(".tmp").exists()


def test_write_overwrites_existing(tmp_path: Path):
    path = tmp_path / "page.md"
    page_v1 = _make_page(content="# V1")
    page_v2 = _make_page(content="# V2", sources=["content_123", "content_456"])

    write_page(path, page_v1)
    write_page(path, page_v2)

    loaded = read_page(path)
    assert loaded.content == "# V2"
    assert loaded.sources == ["content_123", "content_456"]


def test_read_preserves_multiline_content(tmp_path: Path):
    content = "# RAG\n\n## How It Works\n\n1. Index\n2. Retrieve\n3. Generate"
    page = _make_page(content=content)
    path = tmp_path / "page.md"

    write_page(path, page)
    loaded = read_page(path)

    assert loaded.content == content


def test_read_empty_lists(tmp_path: Path):
    page = _make_page(related=[], sources=[])
    path = tmp_path / "page.md"

    write_page(path, page)
    loaded = read_page(path)

    assert loaded.related == []
    assert loaded.sources == []
