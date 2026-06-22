"""Wiki synthesis for one item — plain functions, no graph, no checkpointer.

`synthesize_item` runs the whole thing:

    extract(item) ─→ resolve_or_mint(candidates) ─→ synthesize_entity(e) per
    resolved entity ─→ persist(batch) ─→ write .md files

- **extract** — snapshot the known-entity index, call the extraction LLM, turn
  each `ExtractedEntity` into a `Candidate` (the LLM proposes a name + optional
  `matched_id`, never a surrogate). Failures are captured (not raised) so persist
  still writes a `status='error'` processed row.
- **resolve_or_mint_batch** — assign each candidate a surrogate id: reuse an
  existing entity (exact name / alias / validated matched_id) or mint a fresh
  `e_<hex>`. Pure; stages new entities + aliases for persist to INSERT.
- **synthesize_entity** — read/merge the page via the synthesis LLM, parse, run
  the H2-preservation check, build the `WikiPage` IN MEMORY (no disk write yet).
  Every failure mode is caught → an error record; siblings keep going.
- **_persist_graph** — ONE SQLite transaction: new entities (FK parents first)
  + pages + page_sources + aliases, all-or-nothing.
- **write .md, THEN _mark_processed** — files are written after the graph
  commits (so `num_sources` reads the committed ledger directly, no off-by-one),
  and the `processed` row is written LAST. A crash between the graph commit and
  the processed row leaves a recoverable state: entities are committed (a retry
  reuses the same surrogates → no orphan files) and the item stays un-processed,
  so `pending` re-queues it and the write is retried.

Connections are opened short and per-phase so the LLM work never holds a write
lock — matches the wiki.db WAL discipline.

Langfuse tracing: when configured, the whole item runs inside one span named
`wiki_synthesis__<item_id>` (session_id + tags set on the trace); the
`langfuse.openai` drop-in auto-nests the extract + per-entity generations under
it. Unconfigured → a no-op passthrough, no warnings.
"""

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import yaml
from domains.types import IngestItem
from domains.wiki.aliases import AliasStore
from domains.wiki.identity import (
    Candidate,
    EntityRecord,
    ResolvedEntity,
    normalize_name,
    resolve_or_mint_batch,
    shortid,
)
from domains.wiki.io import write_page
from domains.wiki.state import (
    build_entity_index,
    connection,
    count_sources_for_entity,
    get_aliases_for_entity,
    get_entity,
    insert_aliases,
    insert_entity,
    insert_page_source,
    insert_processed,
    snapshot_aliases,
    upsert_page,
)
from domains.wiki.types import ExtractedEntity, ExtractionResult

from workflows.llm import LLMCall, generate_structured_with_usage, generate_with_usage
from workflows.shared.observability import langfuse_enabled
from workflows.wiki_synthesis.parsing import (
    check_h2_preservation,
    parse_llm_page_output,
)
from workflows.wiki_synthesis.prompts import (
    ENTITY_EXTRACTION_SYSTEM,
    ENTITY_EXTRACTION_USER,
    PAGE_SYNTHESIS_SYSTEM,
    PAGE_SYNTHESIS_USER_CREATE,
    PAGE_SYNTHESIS_USER_UPDATE,
)

logger = logging.getLogger(__name__)

EXTRACTION_MODEL = "gpt-4.1-nano"
SYNTHESIS_MODEL = "gpt-4.1-mini"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def synthesize_item(
    item: IngestItem,
    *,
    db_path: Path | str,
    wiki_dir: Path | str,
    rejected_entities: frozenset[str] = frozenset(),
    replay: bool = False,
) -> dict:
    """Synthesize wiki pages for one item end-to-end.

    Returns the run summary: {"llm_calls": [...], "entity_results": [...],
    "status": "ok"|"error"|"skipped"}.

    rejected_entities is the W2.5 denylist — a set of NORMALISED names (the
    surrogate is minted post-extraction, so the denylist can't key on id). A
    candidate is dropped if its extracted name OR its resolved entity's
    canonical name normalises into this set. replay=True tags the Langfuse
    trace 'replay' (vs 'fresh').
    """
    wiki_dir = Path(wiki_dir)
    with _trace(item, replay=replay):
        all_calls: list[LLMCall] = []

        ext = extract(item, db_path=db_path)
        all_calls.extend(ext["llm_calls"])
        candidates: list[Candidate] = ext["candidates"]
        extract_error: str | None = ext.get("extract_error")

        if extract_error:
            _mark_processed(item, db_path=db_path, status="error", error_text=extract_error)
            return {"llm_calls": all_calls, "entity_results": [], "status": "error"}

        resolution = resolve_or_mint_batch(ext["index"], candidates, now=_now_iso())

        with connection(db_path) as conn:
            records = [
                (cand, rec, resolved)
                for cand, rec, resolved in _resolved_records(conn, candidates, resolution)
                if not _is_rejected(cand, rec, rejected_entities)
            ]

        all_ids = [rec.entity_id for _, rec, _ in records]
        results: list[dict] = []
        for _, rec, _ in records:
            siblings = [eid for eid in all_ids if eid != rec.entity_id]
            res = synthesize_entity(item, rec, siblings, wiki_dir=wiki_dir)
            all_calls.extend(res.pop("llm_calls", []))
            results.append(res)

        successes = [r for r in results if r["status"] == "ok"]
        errors = [r for r in results if r["status"] == "error"]
        success_ids = {r["entity_id"] for r in successes}

        if not records:
            status, error_text = "skipped", None
        elif successes:
            status, error_text = "ok", (_summarize_errors(errors) if errors else None)
        else:
            status, error_text = "error", (_summarize_errors(errors) or "all entities failed")

        new_entities = [e for e in resolution.new_entities if e.entity_id in success_ids]
        # Aliases come from the SURVIVING (non-rejected) records only — a rejected
        # candidate that resolved to a shared entity must not leak its aliases.
        new_aliases = [
            (alias, rec.entity_id)
            for _, rec, resolved in records
            if rec.entity_id in success_ids
            for alias in resolved.aliases
        ]

        # Commit the durable graph (entities + pages + ledger + aliases), then
        # write the .md files, then mark the item processed — in that order so a
        # crash leaves a RECOVERABLE state: the entities are committed (a retry
        # reuses the same surrogates → no orphan files), and the item stays
        # un-`processed` so `pending` re-queues it and the write is retried.
        _persist_graph(
            item,
            db_path=db_path,
            new_entities=new_entities,
            successes=successes,
            new_aliases=new_aliases,
        )
        if successes:
            _write_pages(successes, wiki_dir=wiki_dir, db_path=db_path)
        _mark_processed(item, db_path=db_path, status=status, error_text=error_text)

        return {"llm_calls": all_calls, "entity_results": results, "status": status}


def extract(item: IngestItem, *, db_path: Path | str) -> dict:
    """Snapshot the known-entity index → call extraction LLM → build candidates.

    On a DB/LLM/parse failure, returns extract_error in the result so the caller
    still persists a 'status=error' processed row. Without this, a hard failure
    here would leave no DB footprint and Dagster would retry forever.
    """
    llm_calls: list[LLMCall] = []
    try:
        with connection(db_path) as conn:
            index = build_entity_index(conn)
            store = snapshot_aliases(conn)

        known_entities = _snapshot_to_yaml(store) or "(no known entities yet)"
        user_prompt = ENTITY_EXTRACTION_USER.format(
            known_entities=known_entities,
            title=item.title,
            article_text=item.text,
        )
        extraction, call = generate_structured_with_usage(
            user_prompt,
            schema=ExtractionResult,
            system=ENTITY_EXTRACTION_SYSTEM,
            model=EXTRACTION_MODEL,
        )
        llm_calls.append(call)

        candidates = [_to_candidate(e) for e in extraction.entities]
        return {"index": index, "candidates": candidates, "llm_calls": llm_calls}
    except Exception as e:
        logger.exception("extract failed for %s", item.item_id)
        return {
            "index": None,
            "candidates": [],
            "extract_error": f"{type(e).__name__}: {e}",
            "llm_calls": llm_calls,
        }


def synthesize_entity(
    item: IngestItem,
    entity: EntityRecord,
    sibling_ids: list[str],
    *,
    wiki_dir: Path,
) -> dict:
    """End-to-end synthesis for one entity — builds the page in memory only.

    The .md file is written later, after persist commits (write-after-persist).
    Catches every failure mode and returns one result record:
      ok    → {"status": "ok", "entity_id", "page", "file_path", "related_ids", "llm_calls"}
      error → {"status": "error", "entity_id", "error", "llm_calls"}
    """
    llm_calls: list[LLMCall] = []
    try:
        file_path = f"{entity.slug}-{shortid(entity.entity_id)}.md"
        page_path = wiki_dir / file_path
        is_update = page_path.exists()
        existing_page_text = page_path.read_text(encoding="utf-8") if is_update else None

        template = PAGE_SYNTHESIS_USER_UPDATE if is_update else PAGE_SYNTHESIS_USER_CREATE
        fields = dict(
            entity_id=entity.entity_id,
            title=entity.canonical_name,
            page_type=entity.page_type,
            related=", ".join(sibling_ids),
            source_id=item.item_id,
            article_title=item.title,
            article_text=item.text,
        )
        if is_update:
            fields["existing_page"] = existing_page_text
        user_prompt = template.format(**fields)

        call = generate_with_usage(user_prompt, system=PAGE_SYNTHESIS_SYSTEM, model=SYNTHESIS_MODEL)
        llm_calls.append(call)

        new_page = parse_llm_page_output(
            raw=call.content,
            entity_id=entity.entity_id,
            title=entity.canonical_name,
            page_type=entity.page_type,
            related=sibling_ids,
            source_id=item.item_id,
        )

        if is_update:
            check_h2_preservation(page_path, new_page.content)

        return {
            "status": "ok",
            "entity_id": entity.entity_id,
            "page": new_page,
            "file_path": file_path,
            "related_ids": sibling_ids,
            "llm_calls": llm_calls,
        }
    except Exception as e:
        logger.exception("Failed to synthesize entity %s", entity.entity_id)
        return {
            "status": "error",
            "entity_id": entity.entity_id,
            "error": f"{type(e).__name__}: {e}",
            "llm_calls": llm_calls,
        }


def _persist_graph(
    item: IngestItem,
    *,
    db_path: Path | str,
    new_entities: list[EntityRecord],
    successes: list[dict],
    new_aliases: list[tuple[str, str]],
) -> None:
    """Commit the durable graph in ONE transaction — all-or-nothing.

    FK order: new entities first (pages / aliases / page_sources all FK to
    entities), then pages + page_sources for every successful entity, then the
    staged new aliases (ON CONFLICT DO NOTHING). A no-op when there's nothing to
    write (the skipped / all-failed paths). Every write is idempotent so a retry
    of an item whose `processed` row never landed re-runs cleanly.
    """
    if not new_entities and not successes and not new_aliases:
        return
    with connection(db_path) as conn:
        with conn:
            for entity in new_entities:
                insert_entity(conn, entity)
            for r in successes:
                upsert_page(
                    conn,
                    entity_id=r["entity_id"],
                    file_path=r["file_path"],
                    related_ids=r["related_ids"],
                )
                insert_page_source(
                    conn,
                    entity_id=r["entity_id"],
                    item_id=item.item_id,
                    source_type=item.source_type,
                )
            if new_aliases:
                insert_aliases(conn, new_aliases)


def _mark_processed(
    item: IngestItem,
    *,
    db_path: Path | str,
    status: str,
    error_text: str | None,
) -> None:
    """Write the single processed_items row — the LAST step, AFTER the graph is
    committed and the .md files are written. Marking 'ok' only once the files
    exist means a write crash leaves the item un-processed and `pending`
    re-queues it (self-healing) instead of stranding a page with no file."""
    with connection(db_path) as conn:
        with conn:
            insert_processed(
                conn,
                item_id=item.item_id,
                source_type=item.source_type,
                status=status,
                error=error_text,
            )


def _write_pages(successes: list[dict], *, wiki_dir: Path, db_path: Path | str) -> None:
    """Write each successful page's .md AFTER persist committed.

    num_sources comes straight from the page_sources ledger — this item's edge
    is already committed, so the count needs no adjustment.
    """
    with connection(db_path) as conn:
        for r in successes:
            aliases = get_aliases_for_entity(conn, r["entity_id"])
            num_sources = count_sources_for_entity(conn, r["entity_id"])
            write_page(
                wiki_dir / r["file_path"], r["page"], aliases=aliases, num_sources=num_sources
            )


def _resolved_records(
    conn, candidates: list[Candidate], resolution
) -> list[tuple[Candidate, EntityRecord, ResolvedEntity]]:
    """Pair each candidate with its authoritative EntityRecord + ResolvedEntity.

    New entities come from the resolution; reused ids are looked up so the
    page is synthesised under the STORED canonical_name / page_type / slug
    (cross-type dedup: a reused entity keeps its first-sighting identity, not
    this article's proposed page_type). The ResolvedEntity carries the alias
    display forms to register for the entity once the candidate survives.
    """
    new_by_id = {e.entity_id: e for e in resolution.new_entities}
    triples: list[tuple[Candidate, EntityRecord, ResolvedEntity]] = []
    for cand, resolved in zip(candidates, resolution.resolved, strict=True):
        rec = new_by_id.get(resolved.entity_id) or get_entity(conn, resolved.entity_id)
        if rec is not None:
            triples.append((cand, rec, resolved))
    return triples


def _is_rejected(cand: Candidate, rec: EntityRecord, rejected: frozenset[str]) -> bool:
    """Denylist check on normalised names — the extracted surface form OR the
    resolved entity's canonical name landing in the rejected set drops it."""
    if not rejected:
        return False
    return normalize_name(cand.name) in rejected or rec.normalized_name in rejected


def _to_candidate(entity: ExtractedEntity) -> Candidate:
    return Candidate(
        name=entity.title,
        page_type=entity.page_type,
        matched_id=entity.matched_id,
        aliases=list(entity.aliases),
    )


@contextmanager
def _trace(item: IngestItem, *, replay: bool) -> Iterator[None]:
    """One Langfuse span per item; no-op (and silent) when unconfigured.

    Sets the trace name / session_id / tags so all attempts of an item group
    under one session; the `langfuse.openai` drop-in nests each LLM generation
    under this span automatically.
    """
    if not langfuse_enabled():
        yield
        return
    from langfuse import get_client

    client = get_client()
    name = f"wiki_synthesis__{item.item_id}"
    with client.start_as_current_span(name=name):
        client.update_current_trace(
            name=name,
            session_id=item.item_id,
            tags=["wiki_synthesis", item.source_type, "replay" if replay else "fresh"],
        )
        yield


def _snapshot_to_yaml(store: AliasStore) -> str:
    """Serialize the known-entity snapshot (id → canonical + aliases) to YAML
    for the extraction prompt, so the LLM can pick a matched_id."""
    if not store.entries:
        return ""
    data = {
        entity_id: {"canonical": entry.canonical, "aliases": entry.aliases}
        for entity_id, entry in store.entries.items()
    }
    return yaml.dump(data, default_flow_style=False, sort_keys=True, allow_unicode=True)


def _summarize_errors(errors: list[dict]) -> str:
    if not errors:
        return ""
    return "; ".join(f"{e['entity_id']}: {e['error']}" for e in errors)
