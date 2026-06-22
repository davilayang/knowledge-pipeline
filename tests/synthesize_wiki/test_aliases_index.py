"""Tests for the wiki/aliases_index Dagster asset.

Uses the wiki_db + wiki_db_path fixtures to back the asset with a real wiki.db
(SQLite). The asset invokes its compute_fn directly (no full Dagster
materialization) and we inspect the resulting JSON on disk.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock

import dagster as dg
import pytest
from domains.wiki.identity import EntityRecord, normalize_name, slugify
from domains.wiki.state import insert_aliases, insert_entity, upsert_page
from orchestrators.defs.synthesize_wiki.assets import aliases_index
from orchestrators.defs.synthesize_wiki.resources import WikiResource

NOW = "2026-05-12T00:00:00+00:00"


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
    insert_entity(
        conn,
        EntityRecord(
            entity_id=entity_id,
            canonical_name=title,
            normalized_name=normalize_name(title),
            slug=slugify(title),
            page_type=page_type,
            created_at=NOW,
        ),
    )
    upsert_page(
        conn, entity_id=entity_id, file_path=f"{slugify(title)}-{entity_id}.md", related_ids=[]
    )


def test_aliases_index_builds_flat_map(tmp_path: Path, wiki_db, wiki_db_path):
    _seed_page(wiki_db, entity_id="e_chromadb", page_type="tool", title="ChromaDB")
    insert_aliases(wiki_db, [("chroma", "e_chromadb"), ("chroma-db", "e_chromadb")])
    wiki_db.commit()

    wiki = _resource(tmp_path, wiki_db_path)
    result = aliases_index.op.compute_fn.decorated_fn(_ctx(), wiki=wiki)

    assert isinstance(result, dg.MaterializeResult)
    output_path = tmp_path / "wiki" / "_index" / "aliases.json"
    assert output_path.exists()

    flat = json.loads(output_path.read_text(encoding="utf-8"))
    # All keys lowercased; canonical + aliases + the surrogate all resolve to it.
    assert flat["chromadb"] == "e_chromadb"
    assert flat["chroma"] == "e_chromadb"
    assert flat["chroma-db"] == "e_chromadb"
    assert flat["e_chromadb"] == "e_chromadb"
    assert all(k == k.lower() for k in flat)

    md = result.metadata
    assert md["aliases_total"].value == len(flat)
    assert md["entities_total"].value == 1
    assert md["unchanged"].value is False


def test_aliases_index_atomic_write_with_byte_equality_skip(tmp_path: Path, wiki_db, wiki_db_path):
    """Second materialization with identical DB state must report unchanged=True
    and leave the file's mtime intact (no rewrite)."""
    _seed_page(wiki_db, entity_id="e_chromadb", page_type="tool", title="ChromaDB")
    insert_aliases(wiki_db, [("chroma", "e_chromadb")])
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


def test_aliases_index_resolves_entity_with_no_alias_rows(tmp_path: Path, wiki_db, wiki_db_path):
    """An entity with zero alias rows must still resolve — by its surrogate id
    AND by its canonical name — otherwise the consumer agent silently falls
    through to vector recall and the wiki page is unreachable."""
    _seed_page(wiki_db, entity_id="e_loneliness", page_type="concept", title="Loneliness")
    wiki_db.commit()

    wiki = _resource(tmp_path, wiki_db_path)
    result = aliases_index.op.compute_fn.decorated_fn(_ctx(), wiki=wiki)

    flat = json.loads((tmp_path / "wiki" / "_index" / "aliases.json").read_text(encoding="utf-8"))
    assert flat == {"e_loneliness": "e_loneliness", "loneliness": "e_loneliness"}
    assert result.metadata["aliases_total"].value == 2
    assert result.metadata["entities_total"].value == 1


def test_aliases_index_raises_on_collision(tmp_path: Path, wiki_db, wiki_db_path):
    """One entity's canonical name colliding (lowercased) with another's alias →
    dg.Failure with the offending pair."""
    _seed_page(wiki_db, entity_id="e_chroma_a", page_type="tool", title="Chroma")
    _seed_page(wiki_db, entity_id="e_chroma_b", page_type="tool", title="Chroma DB")
    # e_chroma_b claims the alias "Chroma", which lowercases to e_chroma_a's
    # canonical name — a genuine alias→entity ambiguity the index must reject.
    insert_aliases(wiki_db, [("Chroma", "e_chroma_b")])
    wiki_db.commit()

    wiki = _resource(tmp_path, wiki_db_path)

    with pytest.raises(dg.Failure) as excinfo:
        aliases_index.op.compute_fn.decorated_fn(_ctx(), wiki=wiki)
    assert "chroma" in excinfo.value.description.lower()
    md = excinfo.value.metadata
    a = md["entity_a"].value
    b = md["entity_b"].value
    assert {a, b} == {"e_chroma_a", "e_chroma_b"}
