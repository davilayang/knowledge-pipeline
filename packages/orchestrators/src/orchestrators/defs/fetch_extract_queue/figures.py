"""The figure-description channel: anchors out, operator descriptions in.

A converted book chapter carries `![figure](oreilly:…)` anchors where its
diagrams were — the pipeline never holds the pixels. An operator describes them
locally and attaches the descriptions as JSON; injection swaps each anchor for
its description, which is what makes the chapter deliverable. Everything here
matches on the same anchor, so it lives in one module.
"""

import re

# An unresolved figure anchor as the converter emits it, with the caption that
# follows it on the next block. The caption may sit inside a callout, so the
# blockquote marker is optional. A described figure has had its anchor replaced
# by the description, so an anchor still present IS an undescribed figure.
_FIGURE = re.compile(
    r"!\[figure\]\((?P<anchor>oreilly:[^)]+)\)\s*\n+(?:> )?\*\*(?P<caption>[^*]+)\*\*"
)
_ANCHOR = re.compile(r"!\[figure\]\((?P<anchor>oreilly:[^)]+)\)")


def figure_repair_template(markdown: str) -> dict[str, dict[str, str]]:
    """Every undescribed figure in `markdown`, keyed by the anchor an injector
    will match on, with the caption beside it so an operator can tell which
    picture is which.

    Keyed by the full anchor rather than a short alias: the key is what the
    description has to be matched back to, and an alias would reintroduce the
    mapping step the template exists to remove.
    """
    captions = {m.group("anchor"): m.group("caption").strip() for m in _FIGURE.finditer(markdown)}
    return {
        anchor: {"caption": captions.get(anchor, ""), "description": ""}
        for anchor in dict.fromkeys(m.group("anchor") for m in _ANCHOR.finditer(markdown))
    }


def inject_figure_descriptions(
    markdown: str, figure_text: dict[str, dict[str, str]]
) -> tuple[str, list[str]]:
    """Replace each anchor that has a non-empty description with a delimited
    description block. Returns the body and the anchors that were described.

    An anchor with no entry, or an entry whose description is still empty, is
    left alone — it is what the gate counts, so a half-filled map parks the row
    again rather than delivering a chapter with holes in it.
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
