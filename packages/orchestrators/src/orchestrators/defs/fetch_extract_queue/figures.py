"""The figure-description channel: anchors out, operator descriptions in.

A converted chapter carries `![figure](oreilly:…)` anchors where its diagrams
were — the pipeline never holds the pixels. An operator describes them and
attaches the descriptions as JSON, which injection puts where each anchor was.
"""

import re

# An anchor plus the caption on the block after it; the caption may sit inside a
# callout, so the blockquote marker is optional. An anchor still present is an
# undescribed figure — injection replaces the ones that have a description.
_FIGURE = re.compile(
    r"!\[figure\]\((?P<anchor>oreilly:[^)]+)\)\s*\n+(?:> )?\*\*(?P<caption>[^*]+)\*\*"
)
_ANCHOR = re.compile(r"!\[figure\]\((?P<anchor>oreilly:[^)]+)\)")


def figure_repair_template(markdown: str) -> dict[str, dict[str, str]]:
    """Every undescribed figure, keyed by its anchor, with the caption beside it
    so an operator can tell which picture is which.

    The full anchor rather than a short alias: an alias would reintroduce the
    mapping step this template exists to remove.
    """
    captions = {m.group("anchor"): m.group("caption").strip() for m in _FIGURE.finditer(markdown)}
    return {
        anchor: {"caption": captions.get(anchor, ""), "description": ""}
        for anchor in dict.fromkeys(m.group("anchor") for m in _ANCHOR.finditer(markdown))
    }


def inject_figure_descriptions(
    markdown: str, figure_text: dict[str, dict[str, str]]
) -> tuple[str, list[str]]:
    """Replace each described anchor with a delimited description block.
    Returns the body and the anchors that were described.

    An anchor with no description is left in place — the gate counts anchors, so
    a half-filled map parks the row rather than delivering a chapter with holes.
    """
    described: list[str] = []

    def swap(match: re.Match[str]) -> str:
        anchor = match.group("anchor")
        description = (figure_text.get(anchor, {}) or {}).get("description", "").strip()
        if not description:
            return match.group(0)
        described.append(anchor)
        return f'<figure-description ref="{anchor}">\n{description}\n</figure-description>'

    return _ANCHOR.sub(swap, markdown), described
