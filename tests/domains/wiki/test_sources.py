"""WikiSource reads synthesized wiki .md pages → IngestItems.

Fixtures are written with the real `domains.wiki.io.write_page`, so the test
exercises the true writer→adapter contract (the adapter must read exactly the
frontmatter the synthesis pipeline emits).
"""

from datetime import date
from pathlib import Path

from domains.wiki.io import write_page
from domains.wiki.sources import WikiSource
from domains.wiki.types import WikiPage


def _write(
    wiki_dir: Path,
    *,
    entity_id: str,
    title: str,
    page_type: str,
    summary: str,
    num_sources: int,
    related: list[str] | None = None,
    sources: list[str] | None = None,
) -> None:
    slug = entity_id.split("__", 1)[1]
    path = wiki_dir / page_type / f"{slug}.md"
    page = WikiPage(
        entity_id=entity_id,
        title=title,
        page_type=page_type,
        summary=summary,
        related=related or [],
        sources=sources or [],
        updated_at=date(2026, 6, 20),
        content=f"# {title}\n\nBody.",
    )
    write_page(path, page, aliases=[], num_sources=num_sources)


class TestGetItems:
    def test_page_maps_to_ingest_item_with_summary_as_text(self, tmp_path: Path):
        wiki_dir = tmp_path / "wiki"
        _write(
            wiki_dir,
            entity_id="concept__context_rot",
            title="Context rot",
            page_type="concept",
            summary="Context rot is the degradation of LLM output as the context window fills.",
            num_sources=2,
        )

        items = WikiSource(wiki_dir).get_items()

        assert len(items) == 1
        item = items[0]
        assert item.item_id == "concept__context_rot"
        assert item.source_type == "wiki"
        assert item.text == (
            "Context rot is the degradation of LLM output as the context window fills."
        )

    def test_num_sources_carried_from_frontmatter(self, tmp_path: Path):
        # num_sources is the W3 sparsity-gate signal — it lives in the page
        # frontmatter (producer-authoritative) but not on WikiPage, so the
        # adapter must read it through onto the IngestItem.
        wiki_dir = tmp_path / "wiki"
        _write(
            wiki_dir,
            entity_id="concept__context_rot",
            title="Context rot",
            page_type="concept",
            summary="A summary.",
            num_sources=3,
        )

        item = WikiSource(wiki_dir).get_items()[0]

        assert item.num_sources == 3


class TestGetItemIds:
    def test_returns_entity_ids_sorted(self, tmp_path: Path):
        wiki_dir = tmp_path / "wiki"
        _write(
            wiki_dir,
            entity_id="tool__yazi",
            title="Yazi",
            page_type="tool",
            summary="s",
            num_sources=1,
        )
        _write(
            wiki_dir,
            entity_id="concept__rag",
            title="RAG",
            page_type="concept",
            summary="s",
            num_sources=1,
        )

        assert WikiSource(wiki_dir).get_item_ids() == ["concept__rag", "tool__yazi"]


class TestGetItem:
    def test_returns_item_for_known_entity_id(self, tmp_path: Path):
        wiki_dir = tmp_path / "wiki"
        _write(
            wiki_dir,
            entity_id="concept__rag",
            title="RAG",
            page_type="concept",
            summary="A summary.",
            num_sources=2,
        )

        item = WikiSource(wiki_dir).get_item("concept__rag")

        assert item is not None
        assert item.item_id == "concept__rag"
        assert item.num_sources == 2

    def test_returns_none_for_unknown(self, tmp_path: Path):
        wiki_dir = tmp_path / "wiki"
        _write(
            wiki_dir,
            entity_id="concept__rag",
            title="RAG",
            page_type="concept",
            summary="s",
            num_sources=1,
        )

        assert WikiSource(wiki_dir).get_item("concept__nope") is None


class TestCollidedPage:
    """A known dedup-track failure mode: a page's frontmatter entity_id can
    disagree with its on-disk path (e.g. `concept__x` frontmatter living at
    `trend/x.md`). get_item must stay frontmatter-authoritative — never return
    a page whose entity_id differs from the request, and still resolve the id
    that get_item_ids advertises."""

    def _write_at(self, path: Path, *, entity_id: str, page_type: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        page = WikiPage(
            entity_id=entity_id,
            title="Boring but valuable",
            page_type=page_type,
            summary="A summary.",
            related=[],
            sources=[],
            updated_at=date(2026, 6, 20),
            content="# Boring but valuable\n\nBody.",
        )
        write_page(path, page, aliases=[], num_sources=1)

    def test_request_for_path_implied_id_does_not_return_wrong_page(self, tmp_path: Path):
        # File at trend/boring_but_valuable.md but frontmatter is concept__...
        wiki_dir = tmp_path / "wiki"
        self._write_at(
            wiki_dir / "trend" / "boring_but_valuable.md",
            entity_id="concept__boring_but_valuable",
            page_type="trend",
        )

        # The path-implied id (trend__...) is NOT what the file holds — must NOT
        # return the concept page under a trend id.
        assert WikiSource(wiki_dir).get_item("trend__boring_but_valuable") is None

    def test_advertised_id_is_retrievable_despite_path_mismatch(self, tmp_path: Path):
        wiki_dir = tmp_path / "wiki"
        self._write_at(
            wiki_dir / "trend" / "boring_but_valuable.md",
            entity_id="concept__boring_but_valuable",
            page_type="trend",
        )
        source = WikiSource(wiki_dir)

        # get_item_ids advertises the frontmatter id; get_item must resolve it.
        assert source.get_item_ids() == ["concept__boring_but_valuable"]
        item = source.get_item("concept__boring_but_valuable")
        assert item is not None
        assert item.item_id == "concept__boring_but_valuable"


class TestIndexDirExcluded:
    def test_underscore_dirs_are_not_enumerated(self, tmp_path: Path):
        # _index/ holds sidecars (aliases.json, TOC) with no page frontmatter —
        # enumerating them would crash read_meta. They must be skipped.
        wiki_dir = tmp_path / "wiki"
        _write(
            wiki_dir,
            entity_id="concept__rag",
            title="RAG",
            page_type="concept",
            summary="s",
            num_sources=1,
        )
        index_dir = wiki_dir / "_index"
        index_dir.mkdir(parents=True)
        (index_dir / "toc.md").write_text("# Table of contents\n\nno frontmatter here")

        source = WikiSource(wiki_dir)

        assert source.get_item_ids() == ["concept__rag"]
        assert [i.item_id for i in source.get_items()] == ["concept__rag"]
