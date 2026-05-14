"""Tests for the wiki/aliases_index Dagster asset.

Uses the wiki_pg + wiki_pg_url fixtures to back the asset with a real
Postgres. The asset invokes its compute_fn directly (no full Dagster
materialization) and we inspect the resulting JSON on disk.
"""

import json
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


def _seed_page(conn, *, entity_id: str, page_type: str, title: str) -> None:
    page = WikiPage(
        entity_id=entity_id,
        title=title,
        page_type=page_type,
        summary="",
        related=[],
        sources=[],
        updated_at=__import__("datetime").date(2026, 5, 12),
        content="",
    )
    upsert_page(
        conn,
        page=page,
        file_path=f"{page_type}/{entity_id.split('__', 1)[1]}.md",
        source_types=["raw_store"],
    )


def test_aliases_index_builds_flat_map(tmp_path: Path, wiki_pg, wiki_pg_url):
    _seed_page(wiki_pg, entity_id="tool__chromadb", page_type="tool", title="ChromaDB")
    insert_aliases_idempotent(
        wiki_pg,
        [("tool__chromadb", "ChromaDB", ["chroma", "chroma-db"])],
    )
    wiki_pg.commit()

    wiki = WikiResource(
        wiki_dir=str(tmp_path / "wiki"),
        backup_dir=str(tmp_path / "backup"),
        database_url=wiki_pg_url,
    )
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


def test_aliases_index_atomic_write_with_byte_equality_skip(tmp_path: Path, wiki_pg, wiki_pg_url):
    """Second materialization with identical DB state must report unchanged=True
    and leave the file's mtime intact (no rewrite)."""
    _seed_page(wiki_pg, entity_id="tool__chromadb", page_type="tool", title="ChromaDB")
    insert_aliases_idempotent(wiki_pg, [("tool__chromadb", "ChromaDB", ["chroma"])])
    wiki_pg.commit()

    wiki = WikiResource(
        wiki_dir=str(tmp_path / "wiki"),
        backup_dir=str(tmp_path / "backup"),
        database_url=wiki_pg_url,
    )

    aliases_index.op.compute_fn.decorated_fn(_ctx(), wiki=wiki)
    output_path = tmp_path / "wiki" / "_index" / "aliases.json"
    first_mtime = output_path.stat().st_mtime_ns

    result = aliases_index.op.compute_fn.decorated_fn(_ctx(), wiki=wiki)
    second_mtime = output_path.stat().st_mtime_ns

    assert result.metadata["unchanged"].value is True
    assert first_mtime == second_mtime
    # No leftover .tmp sibling.
    assert not output_path.with_suffix(".json.tmp").exists()


def test_aliases_index_raises_on_collision(tmp_path: Path, wiki_pg, wiki_pg_url):
    """Two entities sharing a lowercased alias → dg.Failure with the offending pair."""
    _seed_page(wiki_pg, entity_id="tool__chroma_a", page_type="tool", title="Chroma A")
    _seed_page(wiki_pg, entity_id="tool__chroma_b", page_type="tool", title="Chroma B")
    # Seed two alias rows pointing at different entity_ids but lowercasing to
    # the same key. The DB unique constraint is on `alias` exactly, not
    # lower(alias), so this is reachable in practice.
    wiki_pg.execute(
        "INSERT INTO wiki.aliases (entity_id, canonical_name, alias) VALUES (%s, %s, %s)",
        ("tool__chroma_a", "Chroma A", "Chroma"),
    )
    wiki_pg.execute(
        "INSERT INTO wiki.aliases (entity_id, canonical_name, alias) VALUES (%s, %s, %s)",
        ("tool__chroma_b", "Chroma B", "chroma"),
    )
    wiki_pg.commit()

    wiki = WikiResource(
        wiki_dir=str(tmp_path / "wiki"),
        backup_dir=str(tmp_path / "backup"),
        database_url=wiki_pg_url,
    )

    with pytest.raises(dg.Failure) as excinfo:
        aliases_index.op.compute_fn.decorated_fn(_ctx(), wiki=wiki)
    assert "chroma" in excinfo.value.description.lower()
    md = excinfo.value.metadata
    # The two entity_ids both appear in the metadata for triage.
    a = md["entity_a"].value
    b = md["entity_b"].value
    assert {a, b} == {"tool__chroma_a", "tool__chroma_b"}
