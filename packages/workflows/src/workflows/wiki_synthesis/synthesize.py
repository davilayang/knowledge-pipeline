"""Wiki synthesis for one item — plain functions, no graph, no checkpointer.

`synthesize_item` runs the whole thing:

    extract(item) ─→ synthesize_entity(e) per kept entity ─→ persist(batch)

- **extract** — snapshot aliases, call the extraction LLM, drop denylisted
  entities, stage new aliases. Failures are captured (not raised) so persist
  still writes a `status='error'` processed row.
- **synthesize_entity** — read/merge the page via the synthesis LLM, parse,
  H2-preservation check, write the .md atomically, return a result record.
  Every failure mode is caught → an error record; siblings keep going.
- **persist** — ONE SQLite transaction: pages + page_sources + aliases +
  the single processed row, all-or-nothing.

Connections are opened short and per-phase (snapshot / counts / commit) so the
LLM work never holds a write lock — matches the wiki.db WAL discipline.

Langfuse tracing: when configured, the whole item runs inside one span named
`wiki_synthesis__<item_id>` (session_id + tags set on the trace); the
`langfuse.openai` drop-in auto-nests the extract + per-entity generations under
it. Unconfigured → a no-op passthrough, no warnings.
"""

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import yaml
from domains.types import IngestItem
from domains.wiki.aliases import AliasStore
from domains.wiki.io import write_page
from domains.wiki.state import (
    connection,
    count_sources_for_entity,
    get_aliases_for_entity,
    insert_aliases_idempotent,
    insert_page_source,
    insert_processed,
    is_source_for_entity,
    snapshot_aliases,
    upsert_page,
)
from domains.wiki.types import ExtractedEntity, ExtractionResult

from workflows.llm import LLMCall, generate_structured_with_usage, generate_with_usage
from workflows.shared.observability import langfuse_enabled
from workflows.wiki_synthesis.parsing import (
    check_h2_preservation,
    parse_llm_page_output,
    slug_from_id,
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

    rejected_entities is the W2.5 denylist — any extracted entity_id in this set
    is skipped (no page built or updated). replay=True tags the Langfuse trace
    'replay' (vs 'fresh') for filtering retries.
    """
    wiki_dir = Path(wiki_dir)
    with _trace(item, replay=replay):
        all_calls: list[LLMCall] = []

        ext = extract(item, db_path=db_path, rejected_entities=rejected_entities)
        all_calls.extend(ext["llm_calls"])
        entities: list[ExtractedEntity] = ext["entities"]
        staged_aliases = ext["staged_aliases"]
        extract_error: str | None = ext.get("extract_error")

        results: list[dict] = []
        for entity in entities:
            sibling_ids = [e.entity_id for e in entities if e.entity_id != entity.entity_id]
            res = synthesize_entity(item, entity, sibling_ids, wiki_dir=wiki_dir, db_path=db_path)
            all_calls.extend(res.pop("llm_calls", []))
            results.append(res)

        successes = [r for r in results if r["status"] == "ok"]
        errors = [r for r in results if r["status"] == "error"]

        if extract_error:
            status, error_text = "error", extract_error
        elif not entities:
            status, error_text = "skipped", None
        elif successes:
            status, error_text = "ok", (_summarize_errors(errors) if errors else None)
        else:
            status, error_text = "error", (_summarize_errors(errors) or "all entities failed")

        persist(
            item,
            db_path=db_path,
            successes=successes,
            staged_aliases=staged_aliases,
            status=status,
            error_text=error_text,
        )

        return {"llm_calls": all_calls, "entity_results": results, "status": status}


def extract(
    item: IngestItem,
    *,
    db_path: Path | str,
    rejected_entities: frozenset[str] = frozenset(),
) -> dict:
    """Snapshot aliases → call extraction LLM → drop denylisted → stage aliases.

    On a Postgres/LLM/parse failure, returns extract_error in the result so the
    caller still persists a 'status=error' processed row. Without this, a hard
    failure here would leave no DB footprint and Dagster would retry forever.
    """
    llm_calls: list[LLMCall] = []
    try:
        with connection(db_path) as conn:
            store = snapshot_aliases(conn)

        aliases_yaml = _aliases_to_yaml(store) or "(no existing aliases)"
        user_prompt = ENTITY_EXTRACTION_USER.format(
            aliases_yaml=aliases_yaml,
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

        # W2.5 denylist: drop rejected entity_ids before staging aliases, so
        # entities + staged_aliases stay consistent and an all-rejected batch
        # falls through to the 'skipped' status path.
        rejected = rejected_entities or frozenset()
        kept = [e for e in extraction.entities if e.entity_id not in rejected]
        staged = _stage_alias_updates(store, kept)

        return {"entities": kept, "staged_aliases": staged, "llm_calls": llm_calls}
    except Exception as e:
        logger.exception("extract failed for %s", item.item_id)
        return {
            "entities": [],
            "staged_aliases": [],
            "extract_error": f"{type(e).__name__}: {e}",
            "llm_calls": llm_calls,
        }


def synthesize_entity(
    item: IngestItem,
    entity: ExtractedEntity,
    sibling_ids: list[str],
    *,
    wiki_dir: Path,
    db_path: Path | str,
) -> dict:
    """End-to-end synthesis for one entity. Catches every failure mode.

    Returns one result record:
      ok    → {"status": "ok", "entity_id", "page", "file_path", "llm_calls"}
      error → {"status": "error", "entity_id", "error", "llm_calls"}
    """
    llm_calls: list[LLMCall] = []
    try:
        page_path = wiki_dir / entity.page_type / f"{slug_from_id(entity.entity_id)}.md"
        is_update = page_path.exists()
        existing_page_text = page_path.read_text(encoding="utf-8") if is_update else None

        if is_update:
            user_prompt = PAGE_SYNTHESIS_USER_UPDATE.format(
                entity_id=entity.entity_id,
                title=entity.title,
                page_type=entity.page_type,
                related=", ".join(sibling_ids),
                existing_page=existing_page_text,
                source_id=item.item_id,
                article_title=item.title,
                article_text=item.text,
            )
        else:
            user_prompt = PAGE_SYNTHESIS_USER_CREATE.format(
                entity_id=entity.entity_id,
                title=entity.title,
                page_type=entity.page_type,
                related=", ".join(sibling_ids),
                source_id=item.item_id,
                article_title=item.title,
                article_text=item.text,
            )

        call = generate_with_usage(user_prompt, system=PAGE_SYNTHESIS_SYSTEM, model=SYNTHESIS_MODEL)
        llm_calls.append(call)

        new_page = parse_llm_page_output(
            raw=call.content,
            entity_id=entity.entity_id,
            title=entity.title,
            page_type=entity.page_type,
            related=sibling_ids,
            source_id=item.item_id,
        )

        if is_update:
            check_h2_preservation(page_path, new_page.content)

        # num_sources counts distinct items in the page_sources ledger, plus
        # this tick's item (persist records its edge after synthesis). The +1 is
        # gated on the LEDGER, not new_page.sources — the LLM almost always
        # echoes the current source into its frontmatter, which made the old
        # check skip the +1 and render 0 on every fresh page.
        with connection(db_path) as conn:
            aliases = get_aliases_for_entity(conn, entity.entity_id)
            num_sources = count_sources_for_entity(conn, entity.entity_id)
            if not is_source_for_entity(conn, entity.entity_id, item.item_id):
                num_sources += 1

        write_page(page_path, new_page, aliases=aliases, num_sources=num_sources)

        return {
            "status": "ok",
            "entity_id": entity.entity_id,
            "page": new_page,
            "file_path": str(page_path.relative_to(wiki_dir)),
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


def persist(
    item: IngestItem,
    *,
    db_path: Path | str,
    successes: list[dict],
    staged_aliases: list[tuple[str, str, list[str]]],
    status: str,
    error_text: str | None,
) -> None:
    """Write the whole batch in ONE transaction — all-or-nothing.

    pages + page_sources for every successful entity, the staged new-entity
    aliases (only when ≥1 page succeeded; ON CONFLICT DO NOTHING), and exactly
    one processed row. The .md files were already written by synthesize_entity
    (file-atomic); if this txn rolls back they're stranded but get rewritten on
    the next run (write_page is idempotent for the same content).
    """
    with connection(db_path) as conn:
        with conn:
            for r in successes:
                upsert_page(
                    conn,
                    page=r["page"],
                    file_path=r["file_path"],
                    source_types=[item.source_type],
                )
                insert_page_source(
                    conn,
                    entity_id=r["entity_id"],
                    item_id=item.item_id,
                    source_type=item.source_type,
                )
            if successes:
                insert_aliases_idempotent(conn, staged_aliases)
            insert_processed(
                conn,
                item_id=item.item_id,
                source_type=item.source_type,
                status=status,
                error=error_text,
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


def _stage_alias_updates(
    store: AliasStore, entities: list[ExtractedEntity]
) -> list[tuple[str, str, list[str]]]:
    """Pick out (entity_id, canonical, aliases) tuples worth persisting.

    Only NEW entities (is_new=True) whose entity_id isn't already in the alias
    store contribute. The DB enforces alias uniqueness via ON CONFLICT DO
    NOTHING — this is just the local prefilter so the SQL stays small.
    """
    staged: list[tuple[str, str, list[str]]] = []
    for entity in entities:
        if entity.is_new and entity.entity_id not in store.entries:
            staged.append((entity.entity_id, entity.title, list(entity.aliases)))
    return staged


def _aliases_to_yaml(store: AliasStore) -> str:
    """Serialize an AliasStore to YAML for the extraction prompt."""
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
