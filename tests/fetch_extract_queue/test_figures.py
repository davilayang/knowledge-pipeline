"""Tests for the figure-description channel.

Injection puts each operator-written description where its figure was, which is
what makes the chapter deliverable and what clears the gate — the gate counts
the anchors that remain.
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
    assert (
        f'<figure-description ref="{ANCHOR}">\nOne trace at a time.\n</figure-description>' in out
    )
    assert f"![figure]({ANCHOR})" not in out
    # Nothing but the anchor line moves. The converter's word-sequence check has
    # already passed on this text, so a stray edit here would not be caught again.
    assert (
        out.replace(
            f'<figure-description ref="{ANCHOR}">\nOne trace at a time.\n</figure-description>',
            f"![figure]({ANCHOR})",
        )
        == body
    )
