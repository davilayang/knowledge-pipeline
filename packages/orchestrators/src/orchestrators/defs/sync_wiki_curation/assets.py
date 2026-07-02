# sync_wiki_curation pipeline — PULL curator rejections down, PUSH wiki entities
# up to the Notion "Wiki Pages" review surface. See README.md for the runbook.
#
# PULL runs before PUSH within the job: delete the rejected set first, then push
# the survivors, so we never re-push a row we are about to delete.

from datetime import UTC, datetime
from pathlib import Path

import dagster as dg
from domains.wiki.attributed import count_sources_for_entity
from domains.wiki.io import read_meta
from domains.wiki.state import (
    PageRecord,
    connection,
    get_aliases_for_entity,
    get_all_pages,
    reject_entity,
    upsert_rejected,
)

from orchestrators.config import SYNC_WIKI_CURATION_DAG_VERSION
from orchestrators.defs.shared.resources import WikiResource

from .def_config import WIKI_DB_CONCURRENCY_KEY
from .resources import WikiPagesNotionResource


def _rich_text(value: str) -> dict:
    # An empty rich_text array clears the cell (Notion rejects a text item with
    # empty content), so only wrap non-empty values.
    return {"rich_text": [{"text": {"content": value}}] if value else []}


def _same_instant(a: str | None, b: str | None) -> bool:
    """True iff two ISO-8601 timestamps name the same UTC minute. Used to compare
    a Notion row's stored `Last updated` against the live page.updated_at.

    Minute (not second) precision is REQUIRED: Notion floors a date property to
    the minute on round-trip, so a page.updated_at of ...:10:48 is read back as
    ...:10:00 — a second-precision compare would never match and the push would
    re-write every row every tick. A change between two daily pushes always lands
    in a different minute (and the shared concurrency key keeps synthesis from
    mutating a page mid-push), so minute granularity loses nothing in practice.
    Exact match only: a merely-newer stored value (clock skew / manual edit) is
    NOT equal → the push re-asserts rather than wrongly skipping. None never
    matches (force push)."""
    if not a or not b:
        return False
    try:
        return _to_utc_minute(a) == _to_utc_minute(b)
    except ValueError:
        return False


def _to_utc_minute(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).replace(second=0, microsecond=0)


def _build_page_properties(
    *,
    entity_id: str,
    title: str,
    entity_type: str,
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
        "Page type": {"select": {"name": entity_type}},
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
        "double-effect. Shares the attributed-lane persist's concurrency key (single-writer "
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
        # Display only — the authoritative alias table. The push never writes
        # back to aliases, so it can't affect resolution / next synthesis.
        aliases = {p.entity_id: get_aliases_for_entity(conn, p.entity_id) for p in pages}
    # entity_id is wiki.db's surrogate; a page-backed entity is "active".
    active_ids = {p.entity_id for p in pages}

    refs = wiki_pages_notion.list_pages()
    ref_by_entity = {r.entity_id: r for r in refs if r.entity_id}

    created = 0
    updated = 0
    skipped = 0
    for page in pages:
        ref = ref_by_entity.get(page.entity_id)
        # Skip the write if this row is unchanged since last push. CHANGE-DETECTION
        # INVARIANT: the stored `Last updated` is the page.updated_at we wrote last
        # tick, so an equal timestamp means no producer field moved — which holds
        # ONLY because every producer-field change bumps page.updated_at (the
        # attributed render bumps it on every re-render — see upsert_page). Status is
        # checked too: an orphaned row whose entity is live again must be re-asserted
        # active even when the timestamp matches. If you add a producer column fed by
        # a table that can change without bumping page.updated_at, this skip serves
        # stale data — bump updated_at on that write or switch to a payload hash.
        if ref and ref.page_status == "active" and _same_instant(ref.last_updated, page.updated_at):
            skipped += 1
            continue
        props = _build_page_properties(
            entity_id=page.entity_id,
            title=page.canonical_name,
            entity_type=page.entity_type,
            summary=_page_summary(wiki_dir, page),
            aliases=aliases[page.entity_id],
            source_count=source_counts[page.entity_id],
            updated_at=page.updated_at,
            page_status="active",
        )
        page_id = ref.page_id if ref else None
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
                f"**{created + updated} written** — {created} created, {updated} updated"
                + f"; {skipped} unchanged"
                + (f"; {orphaned} orphaned" if orphaned else "")
            ),
            "created": dg.MetadataValue.int(created),
            "updated": dg.MetadataValue.int(updated),
            "skipped": dg.MetadataValue.int(skipped),
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
