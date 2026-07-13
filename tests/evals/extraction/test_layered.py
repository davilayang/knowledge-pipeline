"""Layered extraction — structure-blind chunking + global-index merge."""

import re

from evals.extraction.coverage import coverage
from evals.extraction.layered import (
    Chunk,
    chunk_units,
    make_layered_extract_fn,
    render_chunk,
)
from evals.extraction.units import citable_units
from evals.extraction.wide import Claim


def test_chunk_units_packs_by_char_budget_with_global_start():
    units = ["aaaa", "bbbb", "cccc", "dddd"]  # 4 chars each
    chunks = chunk_units(units, budget_chars=8, overlap_units=0)
    # budget fits exactly two 4-char units; each chunk records its global start.
    assert [(c.start, len(c.units)) for c in chunks] == [(0, 2), (2, 2)]


def test_chunk_units_overlap_reincludes_previous_tail():
    units = ["aaaa", "bbbb", "cccc", "dddd"]
    chunks = chunk_units(units, budget_chars=8, overlap_units=1)
    # each chunk after the first rewinds by 1 unit → windows overlap by one.
    assert [(c.start, c.start + len(c.units)) for c in chunks] == [(0, 2), (1, 3), (2, 4)]


def test_render_chunk_uses_global_indices():
    # a chunk starting at global unit 5 must number its lines [5],[6] — the model
    # cites global indices so late-chunk claims land in tail deciles.
    chunk = Chunk(start=5, units=["fifth sentence.", "sixth sentence."])
    assert render_chunk(chunk) == "[5] fifth sentence.\n[6] sixth sentence."


def test_layered_merge_preserves_global_index_to_content_alignment():
    # Four sentences, each with a unique number → two chunks. The stub reads the
    # LAST rendered `[i] text` line of each chunk, pulls that line's number, and
    # emits a claim carrying the number and citing i. verify_grounding (via
    # coverage) then checks the number is present in the GLOBAL units[i]. This only
    # holds if render_chunk labeled i with the unit whose content it actually is —
    # a mislabel (wrong global index, or a dropped unit) makes units[i]'s number
    # differ, and the claim goes unsupported. So supported == both chunks proves
    # the `[i]` label ↔ unit-content alignment end to end, not just index survival.
    content = "Alpha has 11 apples. Beta has 22 apples. Gamma has 33 apples. Delta has 44 apples."

    def stub_chunk_fn(numbered: str) -> tuple[dict, int, int]:
        idx, text = re.findall(r"\[(\d+)\]\s+(.*)", numbered)[-1]  # last line of the chunk
        number = re.search(r"\d+", text).group()
        claim = {"text": f"note about {number}", "cited_indices": [int(idx)], "type": "claim"}
        return {"extracted_title": "T", "claims": [claim]}, 1, 1

    extract_fn = make_layered_extract_fn(
        chunk_extract_fn=stub_chunk_fn, budget_chars=44, overlap_units=0
    )
    out, _tin, _tout = extract_fn(content)

    units = citable_units(content)
    cov = coverage(units, [Claim(**c) for c in out["claims"]])
    assert cov["supported_claims"] == 2  # both chunks' claims align index → content
    assert cov["tail_coverage"] == 1.0  # the last chunk cited the reachable tail decile
