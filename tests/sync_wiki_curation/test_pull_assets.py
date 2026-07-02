"""pull_wiki_rejections (S2) — Notion Rejected toggles → local rejected_entities.

For each Rejected=true row the asset resolves the name to a local entity and
deletes it (reject_entity: tombstone the alias-family + cascade + unlink the
.md). A rejection whose entity is already gone just ensures the tombstone row
exists, so re-running is a no-op. The Notion resource is a stub returning the
query_rejected() dict; wiki.db is a real temp SQLite file.
"""

from unittest.mock import MagicMock

import dagster as dg
from domains.wiki.identity import EntityRecord, normalize_name, slugify
from domains.wiki.state import (
    connection,
    get_entity,
    get_rejected,
    insert_entity,
    upsert_page,
)
from orchestrators.defs.shared.resources import WikiResource
from orchestrators.defs.sync_wiki_curation.assets import pull_wiki_rejections

NOW = "2026-06-23T00:00:00Z"


def _wiki(tmp_path) -> WikiResource:
    return WikiResource(
        backup_dir=str(tmp_path),
        wiki_dir=str(tmp_path / "wiki"),
        wiki_db_path=str(tmp_path / "wiki.db"),
    )


def _seed_entity(conn, entity_id, canonical, *, file_path=None):
    conn.execute("DELETE FROM entities WHERE entity_id = ?", (entity_id,))  # idempotent seed
    ent = EntityRecord(
        entity_id=entity_id,
        canonical_name=canonical,
        normalized_name=normalize_name(canonical),
        slug=slugify(canonical),
        page_type="concept",
        created_at=NOW,
    )
    insert_entity(conn, ent)
    if file_path:
        upsert_page(conn, entity_id=entity_id, file_path=file_path, related_ids=[])


def _notion(rejected: dict) -> MagicMock:
    res = MagicMock()
    res.query_rejected.return_value = rejected
    return res


def _invoke(wiki, notion):
    ctx = MagicMock(spec=dg.AssetExecutionContext)
    return pull_wiki_rejections.op.compute_fn.decorated_fn(ctx, wiki=wiki, wiki_pages_notion=notion)


def test_pull_deletes_rejected_entity_and_tombstones(tmp_path):
    wiki = _wiki(tmp_path)
    db_path = wiki.get_db_path()
    wiki_dir = wiki.get_wiki_dir()
    wiki_dir.mkdir(parents=True, exist_ok=True)
    md = wiki_dir / "claude-code-abcd1234.md"
    md.write_text("# Claude Code\n", encoding="utf-8")
    with connection(db_path) as conn, conn:
        _seed_entity(conn, "e_cc", "Claude Code", file_path="claude-code-abcd1234.md")

    notion = _notion({"claude code": {"category": "already_familiar", "reason": "I know it"}})
    result = _invoke(wiki, notion)

    assert isinstance(result, dg.MaterializeResult)
    with connection(db_path) as conn:
        assert get_entity(conn, "e_cc") is None  # deleted
        rejected = {r.normalized_name for r in get_rejected(conn)}
    assert "claude code" in rejected  # tombstoned
    assert not md.exists()  # .md unlinked
    assert result.metadata["deleted"].value == 1


def test_pull_tombstones_when_entity_absent_and_is_idempotent(tmp_path):
    """A Rejected row whose entity doesn't exist locally (already deleted, or
    never minted) just records the tombstone — no crash. Re-running the whole
    pull is a no-op."""
    wiki = _wiki(tmp_path)
    db_path = wiki.get_db_path()
    with connection(db_path) as conn, conn:
        _seed_entity(conn, "e_cc", "Claude Code", file_path=None)

    notion = _notion(
        {
            "claude code": {"category": None, "reason": None},  # live entity → deleted
            "cookie policy": {"category": "site_chrome", "reason": "nav noise"},  # no entity
        }
    )

    first = _invoke(wiki, notion)
    second = _invoke(wiki, notion)  # re-run: must not raise or double-effect

    with connection(db_path) as conn:
        assert get_entity(conn, "e_cc") is None
        rejected = {r.normalized_name for r in get_rejected(conn)}
    assert rejected == {"claude code", "cookie policy"}
    # First run deletes claude code (live) + tombstones cookie policy (absent).
    assert first.metadata["deleted"].value == 1
    assert first.metadata["tombstoned_only"].value == 1
    # Second run: both names already gone → tombstone-only, nothing deleted.
    assert second.metadata["deleted"].value == 0
    assert second.metadata["tombstoned_only"].value == 2
