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
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.runnables import RunnableConfig

from workflows.shared.checkpointer import get_checkpointer
from workflows.shared.observability import get_langfuse_callback
from workflows.wiki_synthesis.graph import WikiSynthesisState, build_wiki_synthesis_graph


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
    callbacks: list[BaseCallbackHandler] = [cb] if cb else []
    metadata = {
        "langfuse_session_id": item.item_id,
        "langfuse_tags": [
            "wiki_synthesis",
            item.source_type,
            "replay" if replay else "fresh",
        ],
    }
    config: RunnableConfig = {
        "configurable": {"thread_id": f"wiki_synthesis__{item.item_id}"},
        "callbacks": callbacks,
        "metadata": metadata,
    }
    state: WikiSynthesisState = {
        "item": item,
        "db_url": db_url,
        "wiki_dir": str(wiki_dir),
    }

    with get_checkpointer(db_url) as checkpointer:
        graph = build_wiki_synthesis_graph().compile(checkpointer=checkpointer)

        # Three thread states matter, distinguished by graph.get_state(config).next:
        #   - non-existent thread     → next = ()         → invoke(state): fresh run
        #   - paused mid-execution    → next = (...,)     → invoke(None) : resume
        #   - successfully ended      → next = ()         → invoke(state): fresh re-run
        #
        # The "ended" case looks identical to "non-existent" — both have empty
        # next. That's intentional. invoke(state) on an ended thread restarts
        # from START on the same thread_id; the checkpointer accumulates
        # multiple successful runs in its history. This matches how Dagster
        # uses this asset: re-materializing a successful item_id (because
        # upstream raw_store content changed, or a manual re-run) SHOULD
        # re-run from scratch with current inputs, not be a no-op.
        #
        # The only state we explicitly handle is "paused", because that's the
        # one where the wrong call (invoke(state)) silently re-fires every
        # LLM call we already paid for. See tests/wiki_synthesis/test_replay.py.
        snapshot = graph.get_state(config)
        if snapshot.next:
            return graph.invoke(None, config=config)
        return graph.invoke(state, config=config)
