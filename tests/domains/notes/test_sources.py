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
