"""Tests for WikiResource.snapshot_path_for.

Pure path derivation — pinned here so a future rename of the snapshot
filename or partition layout doesn't silently break the asset graph.
"""

from pathlib import Path

from orchestrators.defs.synthesize_wiki.resources import WikiResource


def test_snapshot_path_for_derives_from_partition_key(tmp_path: Path):
    wiki = WikiResource(backup_dir=str(tmp_path), database_url="postgresql://x")
    assert wiki.snapshot_path_for("2026-05-08") == tmp_path / "2026-05-08" / "raw_store.db"


def test_snapshot_path_for_does_not_check_existence(tmp_path: Path):
    """Pure derivation — caller is responsible for the missing-file case."""
    wiki = WikiResource(backup_dir=str(tmp_path / "no_such_dir"), database_url="x")
    # Should not raise — no fs touch.
    path = wiki.snapshot_path_for("2026-05-01")
    assert not path.exists()
