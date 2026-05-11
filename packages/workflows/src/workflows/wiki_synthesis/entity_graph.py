"""Per-entity sub-graph for wiki synthesis.

Each Send from the parent graph spawns one sub-graph instance for a single
entity. One node:

  process_entity   reads page_path from disk, runs the synthesis LLM, parses
                   the output, runs the H2 preservation check, writes the .md
                   file atomically, and emits an EntityResult back to the
                   parent's reducer

A failure anywhere inside process_entity (disk read, LLM call, parse, write)
is caught and emitted as an error EntityResult — siblings keep going; only
the failing entity is missing from the parent's commit txn. Replay of the
parent thread re-runs only this sub-graph because LangGraph checkpoints
sub-graph state via nested namespaces.

The two-phase split (check_existing then synthesize_and_write) was tempting
but only the LLM call was inside the try/except — a corrupt existing .md
file would have crashed the whole batch. Single node + broad try/except
gives full per-entity isolation.
"""

import logging
import operator
from pathlib import Path
from typing import Annotated, ReadOnly, Required, TypedDict

from domains.types import IngestItem
from domains.wiki.io import write_page
from domains.wiki.types import ExtractedEntity
from langgraph.graph import END, START, StateGraph

from workflows.llm import LLMCall, generate_with_usage
from workflows.wiki_synthesis.parsing import (
    check_h2_preservation,
    parse_llm_page_output,
    slug_from_id,
)
from workflows.wiki_synthesis.prompts import (
    PAGE_SYNTHESIS_SYSTEM,
    PAGE_SYNTHESIS_USER_CREATE,
    PAGE_SYNTHESIS_USER_UPDATE,
)

logger = logging.getLogger(__name__)

SYNTHESIS_MODEL = "gpt-4.1-mini"


class EntityWorkflowState(TypedDict, total=False):
    # From the parent's Send
    item: Required[ReadOnly[IngestItem]]
    entity: Required[ReadOnly[ExtractedEntity]]
    sibling_entity_ids: Required[ReadOnly[list[str]]]
    wiki_dir: Required[ReadOnly[str]]

    # Output: these lists are concatenated into the parent's matching keys
    # via the parent state's operator.add reducer
    entity_results: list[dict]
    llm_calls: Annotated[list[LLMCall], operator.add]


class EntityWorkflowOutput(TypedDict, total=False):
    """Restricted output schema — only the aggregated lists propagate back.

    Without this, the sub-graph would also try to write item / entity /
    sibling_entity_ids / wiki_dir back into the parent state on completion.
    Multiple Send'd sub-graphs running in parallel would all want to write
    the parent's `item` channel, raising InvalidUpdateError because that
    channel has no reducer (it's a single-value input, not aggregated).
    """

    entity_results: list[dict]
    llm_calls: list[LLMCall]


def process_entity(state: EntityWorkflowState) -> dict:
    """End-to-end synthesis for one entity. Catches every failure mode.

    Returns {"entity_results": [<one result dict>]} so the parent's reducer
    appends a single record into entity_results.
    """
    entity = state["entity"]
    item = state["item"]
    sibling_ids = state["sibling_entity_ids"]
    wiki_dir = Path(state["wiki_dir"])
    # Track LLM calls outside the try block so a downstream raise (parse,
    # H2 check, write) doesn't drop tokens we already paid for.
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

        write_page(page_path, new_page)

        return {
            "entity_results": [
                {
                    "status": "ok",
                    "entity_id": entity.entity_id,
                    "page": new_page,
                    "file_path": str(page_path.relative_to(wiki_dir)),
                }
            ],
            "llm_calls": llm_calls,
        }
    except Exception as e:
        logger.exception("Failed to process entity %s", entity.entity_id)
        return {
            "entity_results": [
                {
                    "status": "error",
                    "entity_id": entity.entity_id,
                    "error": f"{type(e).__name__}: {e}",
                }
            ],
            "llm_calls": llm_calls,
        }


def build_entity_graph():
    """Compile and return the per-entity sub-graph.

    Compiled without a checkpointer — the parent graph's checkpointer covers
    sub-graph state via nested namespaces in LangGraph 1.x.
    """
    builder = StateGraph(EntityWorkflowState, output_schema=EntityWorkflowOutput)
    builder.add_node("process_entity", process_entity)
    builder.add_edge(START, "process_entity")
    builder.add_edge("process_entity", END)
    return builder.compile()
