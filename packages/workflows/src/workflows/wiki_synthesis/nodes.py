"""Parent-graph nodes for wiki synthesis.

The parent graph has two nodes (plus the per-entity sub-graph):
  extract_entities — reads alias snapshot from Postgres, calls extraction LLM,
                     stages new aliases. Failures are captured into state
                     (extract_error) so commit can still write a processed row.
  commit          — terminal node. Opens one Postgres transaction and writes
                     wiki.pages rows for every successful entity, the staged
                     wiki.aliases rows (ON CONFLICT DO NOTHING), and the single
                     wiki.processed row. Either the whole batch lands or none of it.

Plan §Migration phases step 9 requires wiki.processed and the page rows to
commit in the same transaction — that's what fuses persist_aliases and
commit_processed into one node here.
"""

import logging

import psycopg
import yaml
from domains.wiki.aliases import AliasStore
from domains.wiki.state import (
    insert_aliases_idempotent,
    insert_processed,
    snapshot_aliases,
    upsert_page,
)
from domains.wiki.types import ExtractedEntity, ExtractionResult

from workflows.llm import generate_structured
from workflows.wiki_synthesis.prompts import ENTITY_EXTRACTION_SYSTEM, ENTITY_EXTRACTION_USER

logger = logging.getLogger(__name__)

EXTRACTION_MODEL = "gpt-4.1-nano"


def extract_entities(state: dict) -> dict:
    """Snapshot aliases → call extraction LLM → stage new aliases.

    On Postgres or LLM failure, returns extract_error in state so the workflow
    still reaches commit and writes a status='error' processed row. Without
    this, a hard failure here would leave no DB footprint and Dagster would
    keep retrying the same item forever.
    """
    item = state["item"]
    db_url = state["db_url"]

    try:
        with psycopg.connect(db_url) as conn:
            store = snapshot_aliases(conn)

        aliases_yaml = _aliases_to_yaml(store) or "(no existing aliases)"

        user_prompt = ENTITY_EXTRACTION_USER.format(
            aliases_yaml=aliases_yaml,
            title=item.title,
            article_text=item.text,
        )
        extraction: ExtractionResult = generate_structured(
            user_prompt,
            schema=ExtractionResult,
            system=ENTITY_EXTRACTION_SYSTEM,
            model=EXTRACTION_MODEL,
        )

        staged = _stage_alias_updates(store, extraction.entities)

        return {
            "entities": list(extraction.entities),
            "staged_aliases": staged,
        }
    except Exception as e:
        logger.exception("extract_entities failed for %s", item.item_id)
        return {
            "entities": [],
            "staged_aliases": [],
            "extract_error": f"{type(e).__name__}: {e}",
        }


def commit(state: dict) -> dict:
    """Terminal node — single Postgres transaction.

    Writes wiki.pages rows for successful entities, wiki.aliases for staged
    new-entity aliases (ON CONFLICT DO NOTHING — concurrent partitions safe),
    and exactly one wiki.processed row. The .md files were already written
    by sub-graphs via the file-atomic write_page (tmp + os.replace); if the
    txn rolls back here those files are stranded on disk but get rewritten
    on replay (write_page is idempotent for the same content).

    Status mapping:
      extract_error set         → 'error'
      no entities extracted     → 'skipped'
      ≥1 success, no errors     → 'ok'
      ≥1 success, some errors   → 'ok' with error summary in error column
      0 successes, ≥1 errors    → 'error'
    """
    item = state["item"]
    entity_results: list[dict] = state.get("entity_results", [])
    staged_aliases = state.get("staged_aliases", [])
    entities = state.get("entities", [])
    extract_error: str | None = state.get("extract_error")
    db_url = state["db_url"]

    successes = [r for r in entity_results if r["status"] == "ok"]
    errors = [r for r in entity_results if r["status"] == "error"]

    if extract_error:
        status = "error"
        error_text: str | None = extract_error
    elif not entities:
        status = "skipped"
        error_text = None
    elif successes:
        status = "ok"
        error_text = _summarize_errors(errors) if errors else None
    else:
        status = "error"
        error_text = _summarize_errors(errors) or "all entities failed"

    with psycopg.connect(db_url) as conn:
        with conn.transaction():
            for r in successes:
                upsert_page(
                    conn,
                    page=r["page"],
                    file_path=r["file_path"],
                    source_types=[item.source_type],
                )
            if successes:
                # Aliases are only persisted when at least one page succeeds —
                # mirrors the legacy "no aliases on total failure" behavior.
                insert_aliases_idempotent(conn, staged_aliases)
            insert_processed(
                conn,
                item_id=item.item_id,
                source_type=item.source_type,
                status=status,
                error=error_text,
            )

    return {}


def _stage_alias_updates(
    store: AliasStore, entities: list[ExtractedEntity]
) -> list[tuple[str, str, list[str]]]:
    """Pick out (entity_id, canonical, aliases) tuples worth persisting.

    Same rule as the old ingest: only NEW entities (is_new=True) whose entity_id
    isn't already in the alias store contribute. The DB itself enforces alias
    uniqueness via ON CONFLICT DO NOTHING — this is just the local prefilter so
    the SQL stays small.
    """
    staged: list[tuple[str, str, list[str]]] = []
    for entity in entities:
        if entity.is_new and entity.entity_id not in store.entries:
            staged.append((entity.entity_id, entity.title, list(entity.aliases)))
    return staged


def _aliases_to_yaml(store: AliasStore) -> str:
    """Serialize an AliasStore to YAML for the extraction prompt.

    Matches the format save_aliases() writes to disk so the prompt sees the
    same shape it saw before the Postgres migration.
    """
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
