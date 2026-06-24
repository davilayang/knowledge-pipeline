# sync_wiki_curation pipeline — PULL curator rejections down, PUSH wiki entities
# up to the Notion "Wiki Pages" review surface. See README.md for the runbook.
#
# PULL runs before PUSH within the job: delete the rejected set first, then push
# the survivors, so we never re-push a row we are about to delete.

from pathlib import Path

import dagster as dg
from domains.wiki.io import read_meta
from domains.wiki.state import (
    PageRecord,
    connection,
    count_sources_for_entity,
    get_aliases_for_entity,
    get_all_pages,
    reject_entity,
    upsert_rejected,
)

from orchestrators.config import SYNC_WIKI_CURATION_DAG_VERSION
from orchestrators.defs.synthesize_wiki.resources import WikiResource

from .def_config import WIKI_DB_CONCURRENCY_KEY
from .resources import WikiPagesNotionResource


def _rich_text(value: str) -> dict:
    # An empty rich_text array clears the cell (Notion rejects a text item with
    # empty content), so only wrap non-empty values.
    return {"rich_text": [{"text": {"content": value}}] if value else []}


def _build_page_properties(
    *,
    entity_id: str,
    title: str,
    page_type: str,
    summary: str,
    aliases: list[str],
    source_count: int,
    updated_at: str,
    page_status: str,
) -> dict:
    """Build the Notion property payload for one row — PRODUCER columns only,
    matching the live "Wiki Pages" schema. `Entity ID` is wiki.db's surrogate
    entity_id (the upsert + denylist key). `Aliases` is the comma-joined alias
    family (so the curator can reject the whole family knowingly). `Page status`
    is active for a page-backed entity, orphaned for a row whose entity has left
    wiki.pages.

    The curator columns (Rejected / Reject category / Reject reason) are never
    emitted here; that column-ownership split is what stops the sync from
    clobbering the human's edits."""
    return {
        "Title": {"title": [{"text": {"content": title}}]},
        "Entity ID": _rich_text(entity_id),
        "Summary": _rich_text(summary),
        "Aliases": _rich_text(", ".join(aliases)),
        "Source count": {"number": source_count},
        "Page type": {"select": {"name": page_type}},
        "Last updated": {"date": {"start": updated_at}},
        "Page status": {"select": {"name": page_status}},
    }


# The producer columns this DAG owns and writes. Read once from the live Notion
# schema each push to fail loud on drift (a human renaming/removing one) rather
# than writing garbage. Excludes the curator columns by construction.
PRODUCER_PROPERTIES = frozenset(
    {
        "Title",
        "Entity ID",
        "Summary",
        "Aliases",
        "Source count",
        "Page type",
        "Last updated",
        "Page status",
    }
)


@dg.asset(
    key=["wiki", "rejections_pulled"],
    group_name="wiki_curation",
    kinds={"notion", "sqlite"},
    code_version=SYNC_WIKI_CURATION_DAG_VERSION,
    op_tags={"dagster/concurrency_key": WIKI_DB_CONCURRENCY_KEY},
    description=(
        "Pull curator Rejected=true rows from the Notion 'Wiki Pages' DB into "
        "the local rejected_entities table. A name with a live entity is deleted "
        "(reject_entity: alias-family tombstone + cascade + unlink .md); a name "
        "whose entity is already gone just (re)writes the tombstone row. "
        "Idempotent — re-running chases the human's latest edits with no "
        "double-effect. Shares synthesize_wiki's concurrency key (single-writer "
        "wiki.db)."
    ),
)
def pull_wiki_rejections(
    context: dg.AssetExecutionContext,
    wiki: WikiResource,
    wiki_pages_notion: WikiPagesNotionResource,
) -> dg.MaterializeResult:
    rejected = wiki_pages_notion.query_rejected()
    db_path = wiki.get_db_path()
    wiki_dir = wiki.get_wiki_dir()

    deleted: list[str] = []
    tombstoned_only: list[str] = []
    with connection(db_path) as conn:
        for normalized_name in sorted(rejected):
            meta = rejected[normalized_name]
            row = conn.execute(
                "SELECT entity_id FROM entities WHERE normalized_name = ?",
                (normalized_name,),
            ).fetchone()
            if row is not None:
                entity_id = row["entity_id"]
                with conn:
                    result = reject_entity(
                        conn,
                        entity_id=entity_id,
                        category=meta["category"],
                        reason=meta["reason"],
                    )
                # Commit lands before the unlink (matches wiki-reject): a crash
                # between leaves an orphaned .md, harmless — the entity is gone.
                if result.file_path:
                    (wiki_dir / result.file_path).unlink(missing_ok=True)
                deleted.append(entity_id)
                context.log.info("rejected %s (%s)", entity_id, normalized_name)
            else:
                with conn:
                    upsert_rejected(
                        conn,
                        normalized_name=normalized_name,
                        category=meta["category"],
                        reason=meta["reason"],
                    )
                tombstoned_only.append(normalized_name)

    return dg.MaterializeResult(
        metadata={
            "summary": dg.MetadataValue.md(
                f"**{len(rejected)} rejected** — {len(deleted)} deleted, "
                f"{len(tombstoned_only)} tombstone-only (entity already gone)"
            ),
            "rejected_total": dg.MetadataValue.int(len(rejected)),
            "deleted": dg.MetadataValue.int(len(deleted)),
            "tombstoned_only": dg.MetadataValue.int(len(tombstoned_only)),
        }
    )


@dg.asset(
    key=["wiki", "pages_pushed"],
    group_name="wiki_curation",
    kinds={"sqlite", "notion"},
    code_version=SYNC_WIKI_CURATION_DAG_VERSION,
    deps=[dg.AssetDep(["wiki", "rejections_pulled"])],
    op_tags={"dagster/concurrency_key": WIKI_DB_CONCURRENCY_KEY},
    description=(
        "Project every page-backed wiki.db entity up to the Notion 'Wiki Pages' "
        "DB so the curator has the latest set to review. Upsert keyed on the "
        "Entity ID column (= wiki.db's surrogate entity_id). Writes ONLY producer "
        "columns — never the curator Rejected/Reject* columns. A Notion row whose "
        "entity has left wiki.pages (rejected, merged, removed) is marked Page "
        "status=orphaned, keeping the row + the curator's annotation. Runs AFTER "
        "pull_wiki_rejections so the rejected set is already deleted."
    ),
)
def push_wiki_pages(
    context: dg.AssetExecutionContext,
    wiki: WikiResource,
    wiki_pages_notion: WikiPagesNotionResource,
) -> dg.MaterializeResult:
    # Fail loud on schema drift BEFORE any write — a renamed/removed producer
    # column would otherwise silently drop data into a half-present schema.
    schema_props = wiki_pages_notion.fetch_property_names()
    missing = PRODUCER_PROPERTIES - schema_props
    if missing:
        raise dg.Failure(
            description=(
                f"Notion 'Wiki Pages' schema is missing producer column(s): "
                f"{sorted(missing)}. Restore/rename them before the push writes."
            ),
            metadata={"missing": dg.MetadataValue.json(sorted(missing))},
        )

    wiki_dir = wiki.get_wiki_dir()
    db_path = wiki.get_db_path()
    with connection(db_path) as conn:
        pages = get_all_pages(conn)
        source_counts = {p.entity_id: count_sources_for_entity(conn, p.entity_id) for p in pages}
        # Display only — the authoritative alias table (homonyms suppressed via
        # wiki-merge --no-alias are simply absent here). The push never writes
        # back to aliases, so it can't affect resolution / next synthesis.
        aliases = {p.entity_id: get_aliases_for_entity(conn, p.entity_id) for p in pages}
    # entity_id is wiki.db's surrogate; a page-backed entity is "active".
    active_ids = {p.entity_id for p in pages}

    refs = wiki_pages_notion.list_pages()
    page_id_by_entity = {r.entity_id: r.page_id for r in refs if r.entity_id}

    created = 0
    updated = 0
    for page in pages:
        props = _build_page_properties(
            entity_id=page.entity_id,
            title=page.canonical_name,
            page_type=page.page_type,
            summary=_page_summary(wiki_dir, page),
            aliases=aliases[page.entity_id],
            source_count=source_counts[page.entity_id],
            updated_at=page.updated_at,
            page_status="active",
        )
        page_id = page_id_by_entity.get(page.entity_id)
        wiki_pages_notion.upsert_page(properties=props, page_id=page_id)
        if page_id:
            updated += 1
        else:
            created += 1

    # Orphan any Notion row whose entity is no longer a live page — keep the row
    # (and the curator's reject annotation), just drop it out of the browse view.
    orphaned = 0
    for ref in refs:
        if ref.entity_id and ref.entity_id not in active_ids and ref.page_status != "orphaned":
            wiki_pages_notion.upsert_page(
                properties={"Page status": {"select": {"name": "orphaned"}}},
                page_id=ref.page_id,
            )
            orphaned += 1

    return dg.MaterializeResult(
        metadata={
            "summary": dg.MetadataValue.md(
                f"**{created + updated} active** — {created} created, {updated} updated"
                + (f"; {orphaned} orphaned" if orphaned else "")
            ),
            "created": dg.MetadataValue.int(created),
            "updated": dg.MetadataValue.int(updated),
            "orphaned": dg.MetadataValue.int(orphaned),
            "pages_total": dg.MetadataValue.int(len(pages)),
        }
    )


def _page_summary(wiki_dir: Path, page: PageRecord) -> str:
    """The entity's one-line summary from its `.md` frontmatter, or "" if the
    file is missing/unreadable (still pushed — the row carries the other cols)."""
    try:
        return str(read_meta(wiki_dir / page.file_path).get("summary", "") or "")
    except (OSError, ValueError):
        return ""


all_assets = [pull_wiki_rejections, push_wiki_pages]
