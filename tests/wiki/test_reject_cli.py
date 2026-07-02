"""Tests for the `wiki-reject` CLI orchestration (domains.wiki.reject_cli).

`run_reject` wraps the pure-DB `reject_entity` transaction with the file-system
side: unlink the rejected entity's `.md`. Exercised against a real temp wiki.db
+ temp wiki dir.
"""

from datetime import date

import pytest
from domains.wiki.identity import EntityRecord, normalize_name, slugify
from domains.wiki.io import write_page
from domains.wiki.reject_cli import run_reject
from domains.wiki.state import (
    connection,
    get_entity,
    get_rejected,
    insert_entity,
    upsert_page,
)
from domains.wiki.types import WikiPage

NOW = "2026-06-22T00:00:00+00:00"


def _seed_page(conn, wiki_dir, entity_id, name, file_name):
    insert_entity(
        conn,
        EntityRecord(
            entity_id=entity_id,
            canonical_name=name,
            normalized_name=normalize_name(name),
            slug=slugify(name),
            entity_type="concept",
            created_at=NOW,
        ),
    )
    upsert_page(conn, entity_id=entity_id, file_path=file_name, related_ids=[])
    write_page(
        wiki_dir / file_name,
        WikiPage(
            entity_id=entity_id,
            title=name,
            entity_type="concept",
            summary=f"{name} summary.",
            related=[],
            sources=["art1"],
            updated_at=date(2026, 6, 22),
            content=f"# {name}\n\nbody",
        ),
        aliases=[],
        num_sources=1,
        sources=["art1"],
        related=[],
    )


def test_run_reject_deletes_entity_and_unlinks_file(tmp_path, wiki_db_path):
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()
    with connection(wiki_db_path) as conn, conn:
        _seed_page(conn, wiki_dir, "e_junk", "Cookie Policy", "cookie-policy-aabb.md")

    run_reject(wiki_db_path, wiki_dir, entity_id="e_junk", reason="chrome")

    assert not (wiki_dir / "cookie-policy-aabb.md").exists()
    with connection(wiki_db_path) as conn:
        assert get_entity(conn, "e_junk") is None
        assert [r.normalized_name for r in get_rejected(conn)] == ["cookie policy"]


def test_run_reject_resolves_by_name(tmp_path, wiki_db_path):
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()
    with connection(wiki_db_path) as conn, conn:
        _seed_page(conn, wiki_dir, "e_junk", "Cookie Policy", "cookie-policy-aabb.md")

    run_reject(wiki_db_path, wiki_dir, name="Cookie Policy")

    with connection(wiki_db_path) as conn:
        assert get_entity(conn, "e_junk") is None


def test_run_reject_dry_run_changes_nothing(tmp_path, wiki_db_path):
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()
    with connection(wiki_db_path) as conn, conn:
        _seed_page(conn, wiki_dir, "e_junk", "Cookie Policy", "cookie-policy-aabb.md")

    run_reject(wiki_db_path, wiki_dir, entity_id="e_junk", dry_run=True)

    assert (wiki_dir / "cookie-policy-aabb.md").exists()
    with connection(wiki_db_path) as conn:
        assert get_entity(conn, "e_junk") is not None
        assert get_rejected(conn) == []


def test_run_reject_requires_entity_or_name(tmp_path, wiki_db_path):
    with pytest.raises(ValueError, match="entity_id or name"):
        run_reject(wiki_db_path, tmp_path, dry_run=True)


def test_run_reject_unknown_name_raises(tmp_path, wiki_db_path):
    with pytest.raises(ValueError, match="no entity named"):
        run_reject(wiki_db_path, tmp_path, name="Nonexistent Thing")
