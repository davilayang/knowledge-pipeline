"""Tests for the figure-description channel.

An operator describes a chapter's figures locally and attaches the descriptions
as JSON. Injection puts each description where its figure was, which is what makes the
chapter deliverable — and what clears the gate, since the gate counts the anchors
that remain.
"""

from orchestrators.defs.fetch_extract_queue.figures import inject_figure_descriptions

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
    block = (
        f'<figure-description ref="{ANCHOR}">\n'
        "Figure 10-1 shows:\n"
        "One trace at a time.\n</figure-description>"
    )
    assert block in out
    assert f"![figure]({ANCHOR})" not in out
    # Nothing but the anchor line moves. The converter's word-sequence check has
    # already passed on this text, so a stray edit here would not be caught again.
    assert out.replace(block, f"![figure]({ANCHOR})") == body


def test_the_injected_block_says_which_figure_it_describes():
    """The body has to carry that fact, not a prompt. Measured on a real chapter:
    an unlabelled block left the metadata lane reporting all seven figures
    missing though every one was described; naming the figure cleared it."""
    out, _ = inject_figure_descriptions(
        _body(ANCHOR),
        {ANCHOR: {"caption": "Figure 10-1. A caption for 1.", "description": "Grade buttons."}},
    )

    assert "Figure 10-1 shows:" in out
    assert "Grade buttons." in out


def test_a_figure_with_no_caption_is_still_announced_as_described():
    out, _ = inject_figure_descriptions(
        _body(ANCHOR), {ANCHOR: {"caption": "", "description": "Grade buttons."}}
    )

    assert "The figure here shows:" in out
