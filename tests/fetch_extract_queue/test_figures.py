"""Tests for the figure-description channel.

An operator describes a chapter's figures locally and attaches the descriptions
as JSON. These functions put them into the body and take them back out again:
injection makes the chapter deliverable, stripping keeps a model's reading of a
picture out of the lane that persists claims as things the book said.
"""

from orchestrators.defs.fetch_extract_queue.figures import (
    inject_figure_descriptions,
    strip_figure_descriptions,
)

ANCHOR = "oreilly:9798341660717/aiee_1001.png"
SECOND = "oreilly:9798341660717/aiee_1002.png"


def _body(*anchors: str) -> str:
    parts = ["# Chapter 10. Interfaces for Human Review", "Some prose."]
    for i, anchor in enumerate(anchors, 1):
        parts += [f"![figure]({anchor})", f"**Figure 10-{i}. A caption for {i}.**"]
    return "\n\n".join(parts) + "\n"


def test_a_described_figure_is_replaced_by_a_delimited_description():
    body = _body(ANCHOR)
    out, described = inject_figure_descriptions(
        body, {ANCHOR: {"caption": "Figure 10-1.", "description": "One trace at a time."}}
    )

    assert described == [ANCHOR]
    assert (
        f'<figure-description ref="{ANCHOR}">\nOne trace at a time.\n</figure-description>' in out
    )
    assert f"![figure]({ANCHOR})" not in out
    # Nothing but the anchor line moves: the caption and the prose around it are
    # the publisher's words and a fidelity check downstream compares them.
    assert (
        out.replace(
            f'<figure-description ref="{ANCHOR}">\nOne trace at a time.\n</figure-description>',
            f"![figure]({ANCHOR})",
        )
        == body
    )


def test_a_description_block_is_removed_for_the_claims_lane():
    injected, _ = inject_figure_descriptions(
        _body(ANCHOR), {ANCHOR: {"caption": "", "description": "A model read this picture."}}
    )

    stripped = strip_figure_descriptions(injected)

    assert "A model read this picture." not in stripped
    assert "figure-description" not in stripped
    # The publisher's caption survives — it is the book's own words about the
    # figure, and dropping it would cost the claims lane real content.
    assert "**Figure 10-1. A caption for 1.**" in stripped


def test_stripping_leaves_a_body_with_no_figures_untouched():
    body = "# Chapter 11\n\nProse only.\n"
    assert strip_figure_descriptions(body) == body
