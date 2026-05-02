"""Send-API fan-out actually runs sub-graphs in parallel.

If LangGraph were to silently serialize Send branches (one synth LLM call
finishing before the next starts), the wall-clock benefit of fan-out
collapses to "we wrote a for-loop with extra ceremony." This test rules
that out by routing every mocked synthesis call through a threading.Barrier
that only releases when N threads have arrived. If serialized, only one
thread shows up and the barrier times out → test fails fast.

Plan reference: ai-plannings/2026-05-02_workspace-phase-b-pr2.md → Property 1.
"""

import threading
from pathlib import Path
from unittest.mock import patch

import pytest

from tests.wiki_synthesis._helpers import (
    build_synthesis_output,
    extract_entity_id_from_prompt,
    make_extraction,
    make_item,
)
from workflows.wiki_synthesis.graph import build_wiki_synthesis_graph


@pytest.mark.timeout(15)
def test_send_fan_out_runs_sub_graphs_in_parallel(
    tmp_path: Path, wiki_pg, wiki_pg_url
):
    """All N entity sub-graphs must enter their synthesis call window
    concurrently. The Barrier passes only when all N threads arrive within
    the timeout — if Send runs sequentially, only one thread reaches the
    barrier and the test fails with BrokenBarrierError."""
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()

    n_entities = 4
    barrier = threading.Barrier(n_entities, timeout=5.0)
    arrival_threads: list[str] = []
    arrival_lock = threading.Lock()

    def parallel_generate(prompt, *, system="", model=""):
        eid = extract_entity_id_from_prompt(prompt)
        with arrival_lock:
            arrival_threads.append(threading.current_thread().name)
        # Block until all n_entities threads arrive. Raises BrokenBarrierError
        # on timeout if fewer threads show up (i.e. Send is serialized).
        barrier.wait()
        return build_synthesis_output(eid)

    extraction = make_extraction(*[f"concept__e{i}" for i in range(n_entities)])

    with (
        patch(
            "workflows.wiki_synthesis.nodes.generate_structured",
            return_value=extraction,
        ),
        patch(
            "workflows.wiki_synthesis.entity_graph.generate",
            side_effect=parallel_generate,
        ),
    ):
        graph = build_wiki_synthesis_graph().compile()
        graph.invoke(
            {
                "item": make_item(item_id="parallel_test"),
                "db_url": wiki_pg_url,
                "wiki_dir": str(wiki_dir),
            }
        )

    # If we get here, all 4 calls passed the barrier — fan-out is parallel.
    # Sanity check: at least 2 distinct threads (would catch
    # "everything ran on the main thread sequentially" false positive).
    assert len(set(arrival_threads)) >= 2, (
        f"all {n_entities} synthesis calls ran on a single thread: "
        f"{arrival_threads}. fan-out is not actually concurrent."
    )
