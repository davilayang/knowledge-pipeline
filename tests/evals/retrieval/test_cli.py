"""CLI wiring tests for the wiki source (G1).

These exercise the argument surface + source loading, not the full
embedding/Chroma run (covered by test_runner against fakes).
"""

from datetime import date
from pathlib import Path

from domains.wiki.io import write_page
from domains.wiki.types import WikiPage
from evals.retrieval.cli import _load_items, _parse_args


def _write_wiki_page(wiki_dir: Path, *, entity_id: str, summary: str) -> None:
    page_type, slug = entity_id.split("__", 1)
    page = WikiPage(
        entity_id=entity_id,
        title=slug.replace("_", " "),
        page_type=page_type,
        summary=summary,
        related=[],
        sources=[],
        updated_at=date(2026, 6, 20),
        content=f"# {slug}\n\nBody.",
    )
    write_page(wiki_dir / page_type / f"{slug}.md", page, aliases=[], num_sources=1)


class TestParseArgs:
    def test_accepts_wiki_dir_and_chunker(self, tmp_path: Path):
        args = _parse_args(["--wiki-dir", str(tmp_path), "--chunker-wiki", "markdown"])
        assert args.wiki_dir == tmp_path
        assert args.chunker_wiki == "markdown"


class TestLoadItems:
    def test_loads_wiki_pages_when_wiki_dir_given(self, tmp_path: Path):
        wiki_dir = tmp_path / "wiki"
        _write_wiki_page(
            wiki_dir,
            entity_id="concept__context_rot",
            summary="Context rot degrades long-context output.",
        )

        args = _parse_args(["--wiki-dir", str(wiki_dir)])
        items = _load_items(args)

        assert "wiki" in items
        assert [i.item_id for i in items["wiki"]] == ["concept__context_rot"]
        assert items["wiki"][0].text == "Context rot degrades long-context output."
