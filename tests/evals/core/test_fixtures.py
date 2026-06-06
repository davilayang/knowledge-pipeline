"""JSONL fixture load/save + schema-version header validation."""

import json
from pathlib import Path

import pytest
from evals.core.fixtures import (
    FixtureHeader,
    SchemaVersionMismatch,
    load_fixtures,
    save_fixtures,
)


def _write_jsonl(path: Path, header: dict, rows: list[dict]) -> None:
    with path.open("w") as f:
        f.write(json.dumps(header) + "\n")
        for row in rows:
            f.write(json.dumps(row) + "\n")


def test_load_returns_header_and_rows(tmp_path):
    p = tmp_path / "f.jsonl"
    header = {
        "schema_version": 1,
        "anchored_to_backup": "2026-05-30",
        "fixture_kind": "extraction",
    }
    rows = [{"fixture_id": "yt_001", "content": "..."}, {"fixture_id": "yt_002"}]
    _write_jsonl(p, header, rows)

    h, loaded = load_fixtures(p, expected_schema_version=1)
    assert isinstance(h, FixtureHeader)
    assert h.schema_version == 1
    assert h.anchored_to_backup == "2026-05-30"
    assert len(loaded) == 2


def test_schema_version_mismatch_raises(tmp_path):
    p = tmp_path / "f.jsonl"
    _write_jsonl(p, {"schema_version": 99}, [])
    with pytest.raises(SchemaVersionMismatch):
        load_fixtures(p, expected_schema_version=1)


def test_missing_header_raises(tmp_path):
    p = tmp_path / "f.jsonl"
    p.write_text(json.dumps({"fixture_id": "x"}) + "\n")  # row only, no header
    with pytest.raises(SchemaVersionMismatch):
        load_fixtures(p, expected_schema_version=1)


def test_save_then_load_roundtrip(tmp_path):
    p = tmp_path / "f.jsonl"
    header = FixtureHeader(
        schema_version=2,
        anchored_to_backup="2026-06-01",
        fixture_kind="workflows",
        extra={"content_type_stratification": ["YouTube"]},
    )
    rows = [{"fixture_id": "a"}, {"fixture_id": "b"}]
    save_fixtures(p, header, rows)
    h, loaded = load_fixtures(p, expected_schema_version=2)
    assert h.schema_version == 2
    assert h.extra["content_type_stratification"] == ["YouTube"]
    assert [r["fixture_id"] for r in loaded] == ["a", "b"]


def test_supported_versions_set_accepted(tmp_path):
    """Caller can pass a set to accept multiple schema versions during migration."""
    p = tmp_path / "f.jsonl"
    _write_jsonl(p, {"schema_version": 1}, [{"fixture_id": "x"}])
    _, rows = load_fixtures(p, expected_schema_version={1, 2})
    assert rows == [{"fixture_id": "x"}]
