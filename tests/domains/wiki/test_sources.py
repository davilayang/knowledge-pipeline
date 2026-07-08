"""WikiSource reads synthesized wiki .md pages → IngestItems.

Fixtures are written with the real `domains.wiki.io.write_page` at the real
on-disk layout — flat `{slug}-{shortid}.md` files directly under the wiki dir,
with opaque surrogate `e_<hex>` ids (the layout the synthesis pipeline emits
since the surrogate-identity work). The adapter must read exactly that.
"""

import json
from datetime import date
from pathlib import Path

from domains.wiki.identity import shortid, slugify
from domains.wiki.io import write_page
from domains.wiki.sources import WikiSource
from domains.wiki.types import WikiPage


def _write(
    wiki_dir: Path,
    *,
    entity_id: str,
    title: str,
    summary: str,
    num_sources: int,
    entity_type: str = "concept",
    related: list[str] | None = None,
    sources: list[str] | None = None,
) -> None:
    """Write a page exactly as synthesis does: flat `{slug}-{shortid}.md`."""
    path = wiki_dir / f"{slugify(title)}-{shortid(entity_id)}.md"
    page = WikiPage(
        entity_id=entity_id,
        title=title,
        entity_type=entity_type,
        summary=summary,
        related=related or [],
        sources=sources or [],
        updated_at=date(2026, 6, 20),
        content=f"# {title}\n\nBody.",
    )
    write_page(
        path, page, aliases=[], num_sources=num_sources, sources=page.sources, related=page.related
    )


class TestGetItems:
    def test_page_maps_to_ingest_item_with_summary_as_text(self, tmp_path: Path):
        wiki_dir = tmp_path / "wiki"
        _write(
            wiki_dir,
            entity_id="e_aaaaaaaaaaaaaaaa",
            title="Context rot",
            summary="Context rot is the degradation of LLM output as the context window fills.",
            num_sources=2,
        )

        items = WikiSource(wiki_dir).get_items()

        assert len(items) == 1
        item = items[0]
        assert item.item_id == "e_aaaaaaaaaaaaaaaa"
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
            entity_id="e_aaaaaaaaaaaaaaaa",
            title="Context rot",
            summary="A summary.",
            num_sources=3,
        )

        item = WikiSource(wiki_dir).get_items()[0]

        assert item.num_sources == 3


class TestGetItemIds:
    def test_returns_entity_ids_sorted(self, tmp_path: Path):
        wiki_dir = tmp_path / "wiki"
        _write(wiki_dir, entity_id="e_bbbbbbbbbbbbbbbb", title="Yazi", summary="s", num_sources=1)
        _write(wiki_dir, entity_id="e_aaaaaaaaaaaaaaaa", title="RAG", summary="s", num_sources=1)

        assert WikiSource(wiki_dir).get_item_ids() == [
            "e_aaaaaaaaaaaaaaaa",
            "e_bbbbbbbbbbbbbbbb",
        ]


class TestGetItem:
    def test_returns_item_for_known_entity_id(self, tmp_path: Path):
        wiki_dir = tmp_path / "wiki"
        _write(
            wiki_dir,
            entity_id="e_aaaaaaaaaaaaaaaa",
            title="RAG",
            summary="A summary.",
            num_sources=2,
        )

        item = WikiSource(wiki_dir).get_item("e_aaaaaaaaaaaaaaaa")

        assert item is not None
        assert item.item_id == "e_aaaaaaaaaaaaaaaa"
        assert item.num_sources == 2

    def test_returns_none_for_unknown(self, tmp_path: Path):
        wiki_dir = tmp_path / "wiki"
        _write(wiki_dir, entity_id="e_aaaaaaaaaaaaaaaa", title="RAG", summary="s", num_sources=1)

        assert WikiSource(wiki_dir).get_item("e_dddddddddddddddd") is None


class TestFrontmatterAuthoritative:
    """get_item resolves by the page's frontmatter entity_id, never by anything
    derived from the filename — so a request only returns a page whose
    frontmatter id actually matches, and every id get_item_ids advertises is
    retrievable regardless of what the filename slug/shortid look like."""

    def test_resolves_by_frontmatter_not_filename(self, tmp_path: Path):
        wiki_dir = tmp_path / "wiki"
        # Filename slug/shortid intentionally unrelated to a guessable path.
        path = wiki_dir / "some-misleading-name.md"
        page = WikiPage(
            entity_id="e_aaaaaaaaaaaaaaaa",
            title="RAG",
            entity_type="concept",
            summary="A summary.",
            related=[],
            sources=[],
            updated_at=date(2026, 6, 20),
            content="# RAG\n\nBody.",
        )
        write_page(
            path, page, aliases=[], num_sources=1, sources=page.sources, related=page.related
        )
        source = WikiSource(wiki_dir)

        assert source.get_item_ids() == ["e_aaaaaaaaaaaaaaaa"]
        item = source.get_item("e_aaaaaaaaaaaaaaaa")
        assert item is not None
        assert item.item_id == "e_aaaaaaaaaaaaaaaa"


class TestResolveIndex:
    """resolve_index() surfaces per-entity provenance (page_hash + the tick's
    snapshot_id) from _index/resolve.json — the freshness + staleness signals
    the vector lane stamps onto each embedded page. page_hash lives per-entity;
    snapshot_id is a single top-level tick fingerprint fanned onto every entity."""

    def _write_resolve(self, wiki_dir: Path, entities: dict, snapshot_id: str) -> None:
        index_dir = wiki_dir / "_index"
        index_dir.mkdir(parents=True, exist_ok=True)
        (index_dir / "resolve.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "aliases": {},
                    "entities": entities,
                    "generated_at": "2026-06-20T00:00:00+00:00",
                    "snapshot_id": snapshot_id,
                }
            )
        )

    def test_maps_entity_id_to_page_hash_snapshot_id_num_sources(self, tmp_path: Path):
        wiki_dir = tmp_path / "wiki"
        wiki_dir.mkdir()
        self._write_resolve(
            wiki_dir,
            entities={
                "e_aaaaaaaaaaaaaaaa": {"page_hash": "hash-a", "name": "RAG", "num_sources": 3},
                "e_bbbbbbbbbbbbbbbb": {"page_hash": "hash-b", "name": "Yazi", "num_sources": 1},
            },
            snapshot_id="snap-123",
        )

        index = WikiSource(wiki_dir).resolve_index()

        # resolve.json is the single provenance authority for the vector lane —
        # page_hash + snapshot_id + num_sources all come from the same snapshot so
        # they stay mutually consistent (FM2/FM4).
        assert index == {
            "e_aaaaaaaaaaaaaaaa": {
                "page_hash": "hash-a",
                "snapshot_id": "snap-123",
                "num_sources": 3,
            },
            "e_bbbbbbbbbbbbbbbb": {
                "page_hash": "hash-b",
                "snapshot_id": "snap-123",
                "num_sources": 1,
            },
        }

    def test_absent_resolve_json_returns_empty(self, tmp_path: Path):
        # build_wiki_index and populate_vector_store run independently; a wiki
        # dir without a resolve.json yet must degrade to no-provenance, not crash.
        wiki_dir = tmp_path / "wiki"
        wiki_dir.mkdir()

        assert WikiSource(wiki_dir).resolve_index() == {}


class TestNonPageFilesExcluded:
    def test_index_and_sidecar_dirs_are_not_enumerated(self, tmp_path: Path):
        # index.md (the TOC) and _index/ sidecars (aliases.json) carry no page
        # frontmatter — enumerating them would crash read_meta. They're skipped.
        wiki_dir = tmp_path / "wiki"
        _write(wiki_dir, entity_id="e_aaaaaaaaaaaaaaaa", title="RAG", summary="s", num_sources=1)
        (wiki_dir / "index.md").write_text("# Wiki Index\n\nno frontmatter here")
        sidecar = wiki_dir / "_index"
        sidecar.mkdir()
        (sidecar / "aliases.json").write_text("{}")

        source = WikiSource(wiki_dir)

        assert source.get_item_ids() == ["e_aaaaaaaaaaaaaaaa"]
        assert [i.item_id for i in source.get_items()] == ["e_aaaaaaaaaaaaaaaa"]
