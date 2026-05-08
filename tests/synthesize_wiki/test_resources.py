"""Tests for WikiResource.latest_raw_store_snapshot.

The method scans backup_dir for ISO-date subdirs containing raw_store.db
and returns (path, date) | None. Several failure modes are easy to
regress silently — a typo in the date parser, a missing exists-check on
the db file, the wrong key in max() — none of which are covered by the
existing wiki_synthesis end-to-end tests.
"""

from datetime import date
from pathlib import Path

from orchestrators.defs.pipelines.synthesize_wiki.resources import WikiResource


def _wiki(tmp_path: Path) -> WikiResource:
    """Build a WikiResource pinned to tmp_path with a dummy database_url."""
    return WikiResource(backup_dir=str(tmp_path), database_url="postgresql://x")


def test_returns_none_when_backup_dir_missing(tmp_path: Path):
    wiki = WikiResource(backup_dir=str(tmp_path / "does_not_exist"), database_url="x")
    assert wiki.latest_raw_store_snapshot() is None


def test_returns_none_when_no_valid_subdirs(tmp_path: Path):
    (tmp_path / "foo").mkdir()
    (tmp_path / "2026-13-45").mkdir()  # invalid month
    (tmp_path / "2026-05-01").write_text("not a dir")  # file, not dir
    assert _wiki(tmp_path).latest_raw_store_snapshot() is None


def test_returns_none_when_db_file_absent(tmp_path: Path):
    (tmp_path / "2026-05-01").mkdir()  # date dir but no raw_store.db inside
    assert _wiki(tmp_path).latest_raw_store_snapshot() is None


def test_returns_newest_when_multiple_valid_dirs(tmp_path: Path):
    for d in ("2026-04-30", "2026-05-01", "2026-05-03"):
        (tmp_path / d).mkdir()
        (tmp_path / d / "raw_store.db").write_text("")

    result = _wiki(tmp_path).latest_raw_store_snapshot()
    assert result is not None
    snapshot_path, snapshot_date = result
    assert snapshot_date == date(2026, 5, 3)
    assert snapshot_path == tmp_path / "2026-05-03" / "raw_store.db"


def test_ignores_invalid_dirs_alongside_valid_ones(tmp_path: Path):
    (tmp_path / "2026-05-03").mkdir()
    (tmp_path / "2026-05-03" / "raw_store.db").write_text("")
    # Same-day-newer-looking dir with the legacy timestamp shape — should be ignored.
    (tmp_path / "2026-05-04T12-00-00Z").mkdir()
    (tmp_path / "2026-05-04T12-00-00Z" / "raw_store.db").write_text("")
    # Dir with only an unrelated file, not raw_store.db.
    (tmp_path / "2026-05-05").mkdir()
    (tmp_path / "2026-05-05" / "notes.tgz").write_text("")

    result = _wiki(tmp_path).latest_raw_store_snapshot()
    assert result is not None
    _, snapshot_date = result
    assert snapshot_date == date(2026, 5, 3)
