"""Replay correctness — the load-bearing property of using LangGraph.

If checkpoint-based replay doesn't actually skip completed work, the whole
architectural justification for LangGraph (vs a plain Python loop) collapses.

KEY FINDING (uncovered while writing this test):

LangGraph's Send-API fan-out runs all sub-graphs as ONE atomic super-step.
You cannot interrupt mid-fan-out. The realistic replay scenario is:

  fan-out completes ─→ commit raises ─→ checkpoint reflects post-fan-out state
                                                     │
                                       resume via graph.invoke(None, config)
                                                     │
                                                     ▼
                                             commit re-runs (only)
                                       no entity sub-graphs re-fire

So the original "if entity 7 of 12 fails, replay only redoes entity 7"
claim from the plan was overstated. The accurate property is: "if commit
fails, replay skips the entire fan-out and only re-runs commit." Still
the architecturally important property (no LLM re-calls on commit-time
failure) but the granularity is per-step, not per-entity.

This also means the runner needs a `resume=` parameter so callers
(Dagster retry, manual replay) can call invoke(None, config) instead of
invoke(state, config). PR 3 will add it.

Uses MemorySaver for speed. PostgresSaver has identical checkpoint semantics
in 1.x (verified by reading langgraph-checkpoint base class).
"""

from pathlib import Path
from unittest.mock import patch

import pytest
from langgraph.checkpoint.memory import MemorySaver
from workflows.wiki_synthesis.graph import build_wiki_synthesis_graph

from tests.wiki_synthesis._helpers import (
    build_synthesis_output,
    extract_entity_id_from_prompt,
    make_extraction,
    make_item,
)


@pytest.mark.timeout(15)
def test_commit_failure_then_resume_skips_fan_out(tmp_path: Path, wiki_pg, wiki_pg_url):
    """Pass 1 runs through fan-out, raises in commit. Pass 2 invokes with
    input=None — LangGraph resumes from the checkpoint saved after fan-out
    and re-runs commit only. The synthesis LLM is NOT re-called.

    This is the architecturally important property: a transient DB failure
    during commit doesn't force re-running the (expensive) entity LLM calls.
    """
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()

    extraction = make_extraction(
        "concept__alpha", "concept__beta", "concept__gamma", "concept__delta"
    )

    pass1_calls: list[str] = []
    pass2_calls: list[str] = []
    active_log: list[list[str]] = [pass1_calls]

    def tracking_generate(prompt, *, system="", model=""):
        eid = extract_entity_id_from_prompt(prompt)
        active_log[0].append(eid)
        return build_synthesis_output(eid)

    config = {"configurable": {"thread_id": "wiki_synthesis__commit_replay"}}
    state = {
        "item": make_item(item_id="commit_replay"),
        "db_url": wiki_pg_url,
        "wiki_dir": str(wiki_dir),
    }

    checkpointer = MemorySaver()
    graph = build_wiki_synthesis_graph().compile(checkpointer=checkpointer)

    # PASS 1 — patch insert_processed to raise inside the txn block.
    # The whole commit txn rolls back; the workflow exception propagates.
    with (
        patch(
            "workflows.wiki_synthesis.nodes.generate_structured",
            return_value=extraction,
        ),
        patch(
            "workflows.wiki_synthesis.entity_graph.generate",
            side_effect=tracking_generate,
        ),
        patch(
            "workflows.wiki_synthesis.nodes.insert_processed",
            side_effect=RuntimeError("transient DB failure"),
        ),
    ):
        with pytest.raises(RuntimeError, match="transient DB failure"):
            graph.invoke(state, config=config)

    # All 4 entities ran on pass 1 (fan-out completes before commit attempts)
    assert sorted(set(pass1_calls)) == [
        "concept__alpha",
        "concept__beta",
        "concept__delta",
        "concept__gamma",
    ]

    # The thread is paused waiting on the failed step
    snapshot = graph.get_state(config)
    assert (
        "commit" in snapshot.next
    ), f"expected thread to be paused at 'commit', but next is {snapshot.next}"
    # The post-fan-out state was checkpointed: 4 entity_results landed
    cs = snapshot.values
    assert len(cs.get("entity_results", [])) == 4
    assert cs.get("entities") and len(cs["entities"]) == 4

    # PASS 2 — resume via invoke(None, config). insert_processed is no longer
    # patched, so the txn lands. tracking_generate routes to pass2_calls.
    active_log[0] = pass2_calls
    with (
        patch(
            "workflows.wiki_synthesis.nodes.generate_structured",
            return_value=extraction,
        ),
        patch(
            "workflows.wiki_synthesis.entity_graph.generate",
            side_effect=tracking_generate,
        ),
    ):
        graph.invoke(None, config=config)  # NONE = resume from checkpoint

    # The architecturally important assertion: NO synthesis LLM re-calls on replay.
    # If pass2_calls is non-empty, replay is broken and we have no replay benefit.
    assert pass2_calls == [], (
        f"replay re-ran the synthesis LLM for: {pass2_calls}. Resume did not "
        f"correctly skip the fan-out step."
    )

    # The processed row should now be written
    final = graph.get_state(config)
    # Thread is past END once commit succeeds
    assert final.next == ()


@pytest.mark.timeout(15)
def test_fresh_invocation_does_not_resume(tmp_path: Path, wiki_pg, wiki_pg_url):
    """Sanity check the inverse: invoke(state, config) on a fresh thread
    runs everything from START. Confirms the replay-with-input=None pattern
    is what enables the no-LLM-re-call property — not some quirk of the
    test setup."""
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()

    extraction = make_extraction("concept__only")
    call_count = 0

    def gen(prompt, *, system="", model=""):
        nonlocal call_count
        call_count += 1
        return build_synthesis_output(extract_entity_id_from_prompt(prompt))

    config = {"configurable": {"thread_id": "wiki_synthesis__fresh"}}
    state = {
        "item": make_item(item_id="fresh"),
        "db_url": wiki_pg_url,
        "wiki_dir": str(wiki_dir),
    }
    graph = build_wiki_synthesis_graph().compile(checkpointer=MemorySaver())

    with (
        patch(
            "workflows.wiki_synthesis.nodes.generate_structured",
            return_value=extraction,
        ),
        patch(
            "workflows.wiki_synthesis.entity_graph.generate",
            side_effect=gen,
        ),
    ):
        graph.invoke(state, config=config)

    assert call_count == 1, "fresh invocation should run synthesis once"
