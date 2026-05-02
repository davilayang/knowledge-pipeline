"""Canonical invocation pattern for the wiki_synthesis workflow.

Bundles the four things every caller (Dagster asset, CLI, ad-hoc script)
should configure together:

  - PostgresSaver checkpointer (durable replay across crashes / re-runs)
  - thread_id namespace = "wiki_synthesis__{item_id}"
  - Langfuse callback at graph.invoke level (gives nested traces under one
    parent run instead of orphan per-LLM-call traces)
  - session_id + tags metadata (so Langfuse groups all attempts of the
    same item under one session view)

Callers should prefer this over compiling the graph manually unless they
genuinely need to inject a custom checkpointer (e.g. MemorySaver in
replay tests).
"""

from pathlib import Path

from domains.wiki.sources import IngestItem

from workflows.shared.checkpointer import get_checkpointer
from workflows.shared.observability import get_langfuse_callback
from workflows.wiki_synthesis.graph import build_wiki_synthesis_graph


def invoke_wiki_synthesis(
    item: IngestItem,
    *,
    db_url: str,
    wiki_dir: Path | str,
    replay: bool = False,
) -> dict:
    """Compile and invoke the wiki_synthesis workflow for one item.

    Opens a fresh PostgresSaver from db_url, runs the graph once, closes
    the checkpointer connection on exit. Returns the final parent state
    (entity_results plus inputs).

    Set replay=True after a Dagster retry so the Langfuse trace gets the
    'replay' tag — handy when filtering "show me only fresh first-attempt
    runs" vs "show me retries".
    """
    cb = get_langfuse_callback()
    callbacks = [cb] if cb else []
    metadata = {
        "langfuse_session_id": item.item_id,
        "langfuse_tags": [
            "wiki_synthesis",
            item.source_type,
            "replay" if replay else "fresh",
        ],
    }
    config = {
        "configurable": {"thread_id": f"wiki_synthesis__{item.item_id}"},
        "callbacks": callbacks,
        "metadata": metadata,
    }
    state = {
        "item": item,
        "db_url": db_url,
        "wiki_dir": str(wiki_dir),
    }

    with get_checkpointer(db_url) as checkpointer:
        graph = build_wiki_synthesis_graph().compile(checkpointer=checkpointer)

        # Auto-detect resume: if the thread is paused mid-execution (the prior
        # invocation raised before reaching END), invoke(None) resumes from
        # the last checkpoint. Otherwise pass the full input state to start
        # a fresh run. Crucial because invoke(state) on an existing thread
        # restarts from START and re-runs every entity LLM call — defeating
        # the whole point of checkpointing. See tests/wiki_synthesis/test_replay.py.
        snapshot = graph.get_state(config)
        if snapshot.next:
            return graph.invoke(None, config=config)
        return graph.invoke(state, config=config)
