"""Attributed-lane synthesis orchestration — the layer the Dagster assets call.

Two grains, matching the pipeline topology:

- Per source: `build_source_record` maps a queue_items row to a wiki SourceRecord,
  and `synthesize_source` reads the source's stored claim + candidate docs, runs
  assignment against the live wiki, and persists the attributed claims. This is
  the per-source partitioned step (`persist_attributed_claims`), serialized on
  the shared wiki-write concurrency pool — the resolve-or-mint read snapshot and
  the persist write are not atomic under WAL, so concurrent source partitions
  must not interleave.
- Across sources: `render_entity_pages` re-renders an entity's page from ALL its
  attributed claims (an aggregate over every source), so it runs as an
  unpartitioned sweep (`render_attributed_pages`), not inside a source partition.
"""

import os
from datetime import UTC, date, datetime
from pathlib import Path

from domains.wiki.attributed import (
    SourceRecord,
    attributed_claims_for_entity,
    count_sources_for_entity,
    mint_source_id,
    render_attributed_markdown,
)
from domains.wiki.identity import shortid
from domains.wiki.state import (
    connection,
    get_aliases_for_entity,
    get_all_entities,
    get_entity,
    upsert_page,
)

from workflows.wiki_synthesis.attributed_persist import persist_source_assignment
from workflows.wiki_synthesis.entity_assignment import SubjectMapper, assign_from_stored


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def build_source_record(row: dict, *, now: str | None = None) -> SourceRecord:
    """Map a queue_items row (a `get_row` SELECT * dict) to a wiki SourceRecord.

    `content_key` is the normalized `canonical_url` (falling back to `url` when a
    row was never triaged), so the same article resolves to one source row.
    `origin_type` is 'queue'; `published_at` is the row's `content_date`.
    `publication` is None — queue.db carries no publication field; the rendered
    page attributes by author + URL domain instead."""
    content_key = row.get("canonical_url") or row["url"]
    return SourceRecord(
        source_id=mint_source_id(content_key),
        content_key=content_key,
        origin_type="queue",
        title=row.get("title"),
        author=row.get("author"),
        publication=row.get("publication"),
        url=row.get("url"),
        published_at=row.get("content_date"),
        content_hash=row.get("content_hash"),
        fetched_at=row.get("fetched_at"),
        added_at=now or _now_iso(),
    )


def synthesize_source(
    *,
    claims_doc: str,
    candidates_doc: str,
    source: SourceRecord,
    wiki_db_path: Path | str,
    attribute_subjects: SubjectMapper | None = None,
) -> str:
    """Synthesize one source into wiki.db: assign its stored claims to entities
    (against the LIVE wiki), then persist the source + claims + links.

    `claims_doc` / `candidates_doc` are the source's stored extract-time outputs
    (queue_store `get_claims` / `get_candidates`); `source` is its attribution
    metadata (`build_source_record`). Returns the surviving source_id.

    NOT concurrency-safe on its own: `assign_from_stored` snapshots the entity
    index, then persist writes — two sources resolving the same new entity from
    stale snapshots can both mint it. The caller (the partitioned persist asset)
    serializes runs on the shared wiki-write pool to close that window."""
    assignment = assign_from_stored(
        claims_doc,
        candidates_doc,
        db_path=wiki_db_path,
        attribute_subjects=attribute_subjects,
    )
    with connection(wiki_db_path) as conn, conn:
        return persist_source_assignment(conn, assignment=assignment, source=source)


def render_entity_pages(
    *,
    wiki_db_path: Path | str,
    wiki_dir: Path | str,
    entity_ids: list[str] | None = None,
    updated_at: str | None = None,
) -> list[str]:
    """Render each target entity's page from ALL its attributed claims and write
    it to `wiki_dir` (flat `{slug}-{shortid}.md`), upserting the pages HEAD row.

    The aggregate-over-sources step: an entity's page reflects every source that
    claims about it, so this sweeps entities rather than running per source.
    `entity_ids` scopes the sweep (default: all entities). An entity with no
    attributed claim is skipped (no empty page). Returns the entity_ids written.
    Idempotent — atomic write + `upsert_page`, so a re-render overwrites."""
    wiki_dir = Path(wiki_dir)
    updated_at = updated_at or date.today().isoformat()
    written: list[str] = []
    with connection(wiki_db_path) as conn:
        targets = (
            entity_ids if entity_ids is not None else [e.entity_id for e in get_all_entities(conn)]
        )
        for entity_id in targets:
            entity = get_entity(conn, entity_id)
            if entity is None:
                continue
            claims = attributed_claims_for_entity(conn, entity_id)
            if not claims:
                continue
            markdown = render_attributed_markdown(
                entity=entity,
                claims=claims,
                aliases=get_aliases_for_entity(conn, entity_id),
                num_sources=count_sources_for_entity(conn, entity_id),
                updated_at=updated_at,
            )
            filename = f"{entity.slug}-{shortid(entity.entity_id)}.md"
            path = wiki_dir / filename
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(markdown, encoding="utf-8")
            os.replace(tmp, path)
            with conn:
                upsert_page(conn, entity_id=entity_id, file_path=filename, related_ids=[])
            written.append(entity_id)
    return written
