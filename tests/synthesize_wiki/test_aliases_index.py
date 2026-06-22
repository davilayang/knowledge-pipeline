"""Tests for the wiki/aliases_index Dagster asset.

Uses the wiki_db + wiki_db_path fixtures to back the asset with a real wiki.db
(SQLite). The asset invokes its compute_fn directly (no full Dagster
materialization) and we inspect the resulting JSON on disk.
"""

import json
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

import dagster as dg
import pytest
from domains.wiki.state import insert_aliases_idempotent, upsert_page
from domains.wiki.types import WikiPage
from orchestrators.defs.synthesize_wiki.assets import aliases_index
from orchestrators.defs.synthesize_wiki.resources import WikiResource


def _ctx(partition_key: str = "2026-05-12") -> MagicMock:
    ctx = MagicMock(spec=dg.AssetExecutionContext)
    ctx.partition_key = partition_key
    return ctx


def _resource(tmp_path: Path, wiki_db_path: Path) -> WikiResource:
    return WikiResource(
        wiki_dir=str(tmp_path / "wiki"),
        backup_dir=str(tmp_path / "backup"),
        wiki_db_path=str(wiki_db_path),
    )


def _seed_page(conn, *, entity_id: str, page_type: str, title: str) -> None:
    page = WikiPage(
        entity_id=entity_id,
        title=title,
        page_type=page_type,
        summary="",
        related=[],
        sources=[],
        updated_at=date(2026, 5, 12),
        content="",
    )
    upsert_page(
        conn,
        page=page,
        file_path=f"{page_type}/{entity_id.split('__', 1)[1]}.md",
        source_types=["raw_store"],
    )


def test_aliases_index_builds_flat_map(tmp_path: Path, wiki_db, wiki_db_path):
    _seed_page(wiki_db, entity_id="tool__chromadb", page_type="tool", title="ChromaDB")
    insert_aliases_idempotent(
        wiki_db,
        [("tool__chromadb", "ChromaDB", ["chroma", "chroma-db"])],
    )
    wiki_db.commit()

    wiki = _resource(tmp_path, wiki_db_path)
    result = aliases_index.op.compute_fn.decorated_fn(_ctx(), wiki=wiki)

    assert isinstance(result, dg.MaterializeResult)
    output_path = tmp_path / "wiki" / "_index" / "aliases.json"
    assert output_path.exists()

    flat = json.loads(output_path.read_text(encoding="utf-8"))
    # All keys lowercased; all values map to the same entity_id.
    assert flat["chromadb"] == "tool__chromadb"
    assert flat["chroma"] == "tool__chromadb"
    assert flat["chroma-db"] == "tool__chromadb"
    # No uppercase keys leaked through.
    assert all(k == k.lower() for k in flat)

    md = result.metadata
    assert md["aliases_total"].value == len(flat)
    assert md["entities_total"].value == 1
    assert md["unchanged"].value is False


def test_aliases_index_atomic_write_with_byte_equality_skip(tmp_path: Path, wiki_db, wiki_db_path):
    """Second materialization with identical DB state must report unchanged=True
    and leave the file's mtime intact (no rewrite)."""
    _seed_page(wiki_db, entity_id="tool__chromadb", page_type="tool", title="ChromaDB")
    insert_aliases_idempotent(wiki_db, [("tool__chromadb", "ChromaDB", ["chroma"])])
    wiki_db.commit()

    wiki = _resource(tmp_path, wiki_db_path)

    aliases_index.op.compute_fn.decorated_fn(_ctx(), wiki=wiki)
    output_path = tmp_path / "wiki" / "_index" / "aliases.json"
    first_mtime = output_path.stat().st_mtime_ns

    result = aliases_index.op.compute_fn.decorated_fn(_ctx(), wiki=wiki)
    second_mtime = output_path.stat().st_mtime_ns

    assert result.metadata["unchanged"].value is True
    assert first_mtime == second_mtime
    # No leftover .tmp sibling.
    assert not output_path.with_suffix(".json.tmp").exists()


def test_aliases_index_self_maps_entities_with_no_alias_rows(tmp_path: Path, wiki_db, wiki_db_path):
    """An entity with zero rows in aliases must still be resolvable by its own
    entity_id — otherwise the consumer agent silently falls through to vector
    recall and the wiki page is unreachable."""
    _seed_page(wiki_db, entity_id="concept__loneliness", page_type="concept", title="Loneliness")
    wiki_db.commit()

    wiki = _resource(tmp_path, wiki_db_path)
    result = aliases_index.op.compute_fn.decorated_fn(_ctx(), wiki=wiki)

    flat = json.loads((tmp_path / "wiki" / "_index" / "aliases.json").read_text(encoding="utf-8"))
    assert flat == {"concept__loneliness": "concept__loneliness"}
    assert result.metadata["aliases_total"].value == 1
    assert result.metadata["entities_total"].value == 1


def test_aliases_index_raises_on_collision(tmp_path: Path, wiki_db, wiki_db_path):
    """Two entities sharing a lowercased alias → dg.Failure with the offending pair."""
    _seed_page(wiki_db, entity_id="tool__chroma_a", page_type="tool", title="Chroma A")
    _seed_page(wiki_db, entity_id="tool__chroma_b", page_type="tool", title="Chroma B")
    # Seed two alias rows pointing at different entity_ids but lowercasing to the
    # same key. The UNIQUE constraint is on `alias` exactly, not lower(alias),
    # so this is reachable in practice.
    wiki_db.execute(
        "INSERT INTO aliases (entity_id, canonical_name, alias) VALUES (?, ?, ?)",
        ("tool__chroma_a", "Chroma A", "Chroma"),
    )
    wiki_db.execute(
        "INSERT INTO aliases (entity_id, canonical_name, alias) VALUES (?, ?, ?)",
        ("tool__chroma_b", "Chroma B", "chroma"),
    )
    wiki_db.commit()

    wiki = _resource(tmp_path, wiki_db_path)

    with pytest.raises(dg.Failure) as excinfo:
        aliases_index.op.compute_fn.decorated_fn(_ctx(), wiki=wiki)
    assert "chroma" in excinfo.value.description.lower()
    md = excinfo.value.metadata
    a = md["entity_a"].value
    b = md["entity_b"].value
    assert {a, b} == {"tool__chroma_a", "tool__chroma_b"}
