"""Tests for the book-chapter figure gate.

The gate parks a chapter whose figures carry no description. It counts figure
anchors in the converted body itself rather than reading the extractor's
`unreadable` list, which on real chapters reports nothing at all while up to ten
figures are present — a gate built on that list would pass them.
"""

import dagster as dg
from orchestrators.defs.fetch_extract_queue.checks import (
    figure_gate_result,
    figure_repair_template,
)

ANCHOR = "oreilly:9798341660717/aiee_1001.png"
SECOND = "oreilly:9798341660717/aiee_1002.png"


def _body(*anchors: str) -> str:
    parts = ["# Chapter 10. Interfaces for Human Review", "Some prose."]
    for i, anchor in enumerate(anchors, 1):
        parts += [f"![figure]({anchor})", f"**Figure 10-{i}. A caption for {i}.**"]
    return "\n\n".join(parts) + "\n"


def test_chapter_with_undescribed_figures_fails():
    result = figure_gate_result("book_chapter", _body(ANCHOR))

    assert result.passed is False
    assert result.severity == dg.AssetCheckSeverity.ERROR
    assert result.metadata["figures"].value == 1


def test_chapter_without_figures_passes():
    """evals11 (Data Analysis for Traces) carries none and must complete — it is
    the only chapter that can flow end to end before descriptions exist."""
    result = figure_gate_result("book_chapter", "# Chapter 11\n\nProse only.\n")

    assert result.passed is True
    assert result.metadata["figures"].value == 0


def test_other_content_types_are_never_gated():
    """A medium article carrying something anchor-shaped is not a book chapter."""
    result = figure_gate_result("medium", _body(ANCHOR))

    assert result.passed is True
    assert result.metadata["gated"].value is False


def test_template_is_keyed_by_the_exact_anchor():
    """The key is what an injector matches on later, so an alias would
    reintroduce the mapping step the template exists to remove."""
    template = figure_repair_template(_body(ANCHOR, SECOND))

    assert list(template) == [ANCHOR, SECOND]
    assert template[ANCHOR]["description"] == ""


def test_captions_are_paired_with_their_figure():
    template = figure_repair_template(_body(ANCHOR, SECOND))

    assert template[ANCHOR]["caption"] == "Figure 10-1. A caption for 1."
    assert template[SECOND]["caption"] == "Figure 10-2. A caption for 2."


def test_a_caption_inside_a_callout_still_pairs():
    """A figure inside a note has its caption blockquoted; one real chapter
    (AI Engineering 4) carries exactly this shape."""
    body = f"# Ch\n\n![figure]({ANCHOR})\n\n> **Figure 4-9. Inside a callout.**\n"

    template = figure_repair_template(body)

    assert template[ANCHOR]["caption"] == "Figure 4-9. Inside a callout."


def test_an_uncaptioned_figure_still_needs_a_description():
    """The caption is a convenience for the operator; the anchor is the gate."""
    body = f"# Ch\n\n![figure]({ANCHOR})\n\nPlain prose, no caption.\n"

    template = figure_repair_template(body)

    assert template == {ANCHOR: {"caption": "", "description": ""}}
