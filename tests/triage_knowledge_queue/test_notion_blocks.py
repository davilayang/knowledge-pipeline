"""Tests for triage_knowledge_queue.notion_blocks.blocks_to_markdown."""

from orchestrators.defs.triage_knowledge_queue.notion_blocks import blocks_to_markdown


def _rich(text: str) -> list[dict]:
    return [{"plain_text": text}]


def test_blocks_to_markdown_converts_paragraphs_and_headings() -> None:
    blocks = [
        {"type": "heading_1", "heading_1": {"rich_text": _rich("Title")}},
        {"type": "paragraph", "paragraph": {"rich_text": _rich("First paragraph.")}},
        {"type": "heading_2", "heading_2": {"rich_text": _rich("Section")}},
        {"type": "bulleted_list_item", "bulleted_list_item": {"rich_text": _rich("Point one")}},
        {"type": "quote", "quote": {"rich_text": _rich("Quoted line")}},
        {
            "type": "code",
            "code": {"rich_text": _rich("print('hi')"), "language": "python"},
        },
    ]
    out = blocks_to_markdown(blocks)
    assert "# Title" in out
    assert "## Section" in out
    assert "First paragraph." in out
    assert "- Point one" in out
    assert "> Quoted line" in out
    assert "```python\nprint('hi')\n```" in out


def test_blocks_to_markdown_handles_callout_and_to_do_as_paragraph_like() -> None:
    blocks = [
        {"type": "callout", "callout": {"rich_text": _rich("Callout text")}},
        {"type": "to_do", "to_do": {"rich_text": _rich("A task")}},
    ]
    out = blocks_to_markdown(blocks)
    assert "Callout text" in out
    assert "- A task" in out


def test_blocks_to_markdown_skips_image_embed_table_synced_block_and_divider() -> None:
    blocks = [
        {"type": "paragraph", "paragraph": {"rich_text": _rich("real text")}},
        {"type": "image", "image": {}},
        {"type": "embed", "embed": {}},
        {"type": "table", "table": {}},
        {"type": "synced_block", "synced_block": {}},
        {"type": "divider", "divider": {}},
        {"type": "child_page", "child_page": {}},
        {"type": "paragraph", "paragraph": {"rich_text": _rich("more text")}},
    ]
    out = blocks_to_markdown(blocks)
    assert out == "real text\n\nmore text"


def test_blocks_to_markdown_drops_nested_children_in_v1() -> None:
    """Documents the v1 limitation — top-level rich_text only, no recursion."""
    blocks = [
        {
            "type": "bulleted_list_item",
            "bulleted_list_item": {"rich_text": _rich("Parent bullet")},
            "has_children": True,
        }
    ]
    out = blocks_to_markdown(blocks)
    assert out == "- Parent bullet"


def test_blocks_to_markdown_handles_empty_rich_text_arrays() -> None:
    blocks = [
        {"type": "paragraph", "paragraph": {"rich_text": []}},
        {"type": "paragraph", "paragraph": {"rich_text": _rich("text")}},
    ]
    out = blocks_to_markdown(blocks)
    assert out == "\n\ntext"
