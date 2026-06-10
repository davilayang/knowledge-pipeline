"""Notion block-children → markdown converter.

Top-level only; nested children under list/toggle/callout are NOT recursed
in v1. Add a recursive variant that calls blocks.children.list(block_id=b["id"])
if real pastes show a drop-rate problem.
"""

from typing import Any

_BLOCK_PREFIX = {
    "paragraph": "",
    "heading_1": "# ",
    "heading_2": "## ",
    "heading_3": "### ",
    "bulleted_list_item": "- ",
    "numbered_list_item": "1. ",
    "quote": "> ",
    "callout": "",
    "to_do": "- ",
}


def blocks_to_markdown(blocks: list[dict[str, Any]]) -> str:
    """Convert Notion top-level blocks to markdown.

    Skips: image, embed, child_page, divider, table, synced_block.
    """
    parts: list[str] = []
    for block in blocks:
        block_type = block.get("type", "")
        if block_type == "code":
            text = "".join(rt.get("plain_text") or "" for rt in block["code"].get("rich_text", []))
            lang = block["code"].get("language") or ""
            parts.append(f"```{lang}\n{text}\n```")
        elif block_type in _BLOCK_PREFIX:
            payload = block.get(block_type) or {}
            text = "".join(rt.get("plain_text") or "" for rt in payload.get("rich_text", []))
            parts.append(_BLOCK_PREFIX[block_type] + text)
        # else: silently skipped — see docstring.
    return "\n\n".join(parts)
