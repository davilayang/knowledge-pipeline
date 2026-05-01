"""Parent StateGraph for wiki synthesis. One document per invocation.

Shape (matches plan §Wiki workflow shape):

    START → extract_entities ─┬─ Send fan-out ──┬─→ entity_workflow ─┐
                              │                  └─→ entity_workflow ─┤  (parallel)
                              │                                       │
                              └─→ commit ←───────────────────────────┘
                                    │
                                  END

Send-API fan-out: one Send per ExtractedEntity targets the compiled per-entity
sub-graph (entity_graph.py). LangGraph's reducer (operator.add on
entity_results) concatenates each sub-graph's emitted result list into the
parent state. The commit node then writes the whole batch in one txn.

If extract_entities returns no entities, the conditional edge skips fan-out
and routes straight to commit, which records a status='skipped' processed row.
"""

import operator
from typing import Annotated, TypedDict

from domains.wiki.sources import IngestItem
from domains.wiki.types import ExtractedEntity
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from workflows.wiki_synthesis.entity_graph import build_entity_graph
from workflows.wiki_synthesis.nodes import commit, extract_entities


class WikiSynthesisState(TypedDict, total=False):
    # Inputs (set at invocation)
    item: IngestItem
    db_url: str
    wiki_dir: str

    # Filled by extract_entities
    entities: list[ExtractedEntity]
    staged_aliases: list[tuple[str, str, list[str]]]
    extract_error: str | None  # set if the extraction step itself raised

    # Aggregated from sub-graphs by the reducer
    entity_results: Annotated[list[dict], operator.add]


def fan_out(state: WikiSynthesisState) -> str | list[Send]:
    """Conditional edge: dispatch one sub-graph per entity, or skip to commit."""
    entities = state.get("entities", [])
    if not entities:
        return "commit"
    return [
        Send(
            "entity_workflow",
            {
                "item": state["item"],
                "entity": entity,
                "sibling_entity_ids": [
                    e.entity_id for e in entities if e.entity_id != entity.entity_id
                ],
                "wiki_dir": state["wiki_dir"],
            },
        )
        for entity in entities
    ]


def build_wiki_synthesis_graph():
    """Build (uncompiled) the parent StateGraph.

    Caller compiles with a checkpointer:

        with get_checkpointer() as ck:
            graph = build_wiki_synthesis_graph().compile(checkpointer=ck)
            graph.invoke(
                {"item": item, "db_url": db_url, "wiki_dir": str(wiki_dir)},
                config={"configurable": {"thread_id": f"wiki_synthesis__{item.item_id}"}},
            )
    """
    builder = StateGraph(WikiSynthesisState)
    builder.add_node("extract_entities", extract_entities)
    builder.add_node("entity_workflow", build_entity_graph())
    builder.add_node("commit", commit)

    builder.add_edge(START, "extract_entities")
    builder.add_conditional_edges(
        "extract_entities",
        fan_out,
        ["entity_workflow", "commit"],
    )
    builder.add_edge("entity_workflow", "commit")
    builder.add_edge("commit", END)

    return builder
