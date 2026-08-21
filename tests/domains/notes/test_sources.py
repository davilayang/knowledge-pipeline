from datetime import date
from pathlib import Path

from domains.notes.sources import LocalFileSource


class TestLocalFileSource:
    def test_yields_items_from_md_files(self, tmp_path: Path):
        inbox = tmp_path / "inbox"
        inbox.mkdir()
        (inbox / "notes.md").write_text("# My Notes\n\nSome content here.")

        source = LocalFileSource(inbox_dir=inbox)
        items = source.get_items()

        assert len(items) == 1
        assert items[0].source_type == "local_file"
        assert "Some content here" in items[0].text

    def test_title_from_frontmatter(self, tmp_path: Path):
        inbox = tmp_path / "inbox"
        inbox.mkdir()
        (inbox / "doc.md").write_text("---\ntitle: My Custom Title\n---\n\nBody text.")

        source = LocalFileSource(inbox_dir=inbox)
        items = source.get_items()

        assert items[0].title == "My Custom Title"

    def test_title_from_filename(self, tmp_path: Path):
        inbox = tmp_path / "inbox"
        inbox.mkdir()
        (inbox / "my_cool_notes.md").write_text("Just some text.")

        source = LocalFileSource(inbox_dir=inbox)
        items = source.get_items()

        assert items[0].title == "my cool notes"

    def test_date_from_filename_prefix(self, tmp_path: Path):
        inbox = tmp_path / "inbox"
        inbox.mkdir()
        (inbox / "2026-04-21_meeting.md").write_text("Meeting notes.")

        source = LocalFileSource(inbox_dir=inbox)
        items = source.get_items()

        assert items[0].date == date(2026, 4, 21)

    def test_date_from_frontmatter(self, tmp_path: Path):
        inbox = tmp_path / "inbox"
        inbox.mkdir()
        (inbox / "notes.md").write_text("---\ndate: 2026-03-15\n---\n\nContent.")

        source = LocalFileSource(inbox_dir=inbox)
        items = source.get_items()

        assert items[0].date == date(2026, 3, 15)

    def test_empty_dir(self, tmp_path: Path):
        inbox = tmp_path / "inbox"
        inbox.mkdir()

        source = LocalFileSource(inbox_dir=inbox)
        assert source.get_items() == []

    def test_missing_dir(self, tmp_path: Path):
        source = LocalFileSource(inbox_dir=tmp_path / "nonexistent")
        assert source.get_items() == []

    def test_deterministic_item_id(self, tmp_path: Path):
        inbox = tmp_path / "inbox"
        inbox.mkdir()
        (inbox / "doc.md").write_text("Same content.")

        source = LocalFileSource(inbox_dir=inbox)
        id1 = source.get_items()[0].item_id
        id2 = source.get_items()[0].item_id

        assert id1 == id2


class TestGetItemIdsSkipsBodylessFiles:
    """A markdown file whose body is empty once frontmatter is stripped chunks to
    nothing, so the vector-store job writes no vector for it — and since presence
    in the vector store is that job's only "already done" signal, it re-selects
    the file every tick forever. One such note file was doing exactly that in
    production, leaving the notes lane permanently one item short of complete."""

    def test_frontmatter_only_file_is_not_listed(self, tmp_path: Path):
        inbox = tmp_path / "inbox"
        inbox.mkdir()
        (inbox / "real.md").write_text("---\ntitle: Real\n---\n\nActual body text.")
        (inbox / "meta_only.md").write_text("---\ntitle: Placeholder\ndate: 2026-04-01\n---\n")

        source = LocalFileSource(inbox_dir=inbox)
        ids = source.get_item_ids()

        assert len(ids) == 1
        assert source.get_item(ids[0]).title == "Real"

    def test_blank_file_is_not_listed(self, tmp_path: Path):
        inbox = tmp_path / "inbox"
        inbox.mkdir()
        (inbox / "blank.md").write_text("   \n\n\t\n")

        assert LocalFileSource(inbox_dir=inbox).get_item_ids() == []
