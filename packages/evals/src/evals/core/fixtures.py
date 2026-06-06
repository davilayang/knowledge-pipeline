"""Schema-versioned JSONL fixture load/save.

All eval JSONL files carry a header line with `schema_version`. Loaders
validate the version against an expected set; mismatch raises rather than
silently degrading. Migrations between versions are explicit subcommands
on `eval-corpus migrate-schema` (lands in Step 6).
"""

import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path


class SchemaVersionMismatch(ValueError):
    pass


@dataclass(frozen=True)
class FixtureHeader:
    schema_version: int
    anchored_to_backup: str | None = None
    fixture_kind: str | None = None
    extra: dict = field(default_factory=dict)


def _header_to_dict(h: FixtureHeader) -> dict:
    out: dict = {"schema_version": h.schema_version}
    if h.anchored_to_backup is not None:
        out["anchored_to_backup"] = h.anchored_to_backup
    if h.fixture_kind is not None:
        out["fixture_kind"] = h.fixture_kind
    out.update(h.extra)
    return out


def _dict_to_header(d: dict) -> FixtureHeader:
    known = {"schema_version", "anchored_to_backup", "fixture_kind"}
    extra = {k: v for k, v in d.items() if k not in known}
    return FixtureHeader(
        schema_version=d["schema_version"],
        anchored_to_backup=d.get("anchored_to_backup"),
        fixture_kind=d.get("fixture_kind"),
        extra=extra,
    )


def load_fixtures(
    path: Path, *, expected_schema_version: int | set[int]
) -> tuple[FixtureHeader, list[dict]]:
    """Read the JSONL file at `path`. First line is the header; rest are rows.

    Raises SchemaVersionMismatch if the header is missing schema_version or
    if it isn't in `expected_schema_version` (single int or set).
    """
    accepted = (
        {expected_schema_version}
        if isinstance(expected_schema_version, int)
        else expected_schema_version
    )
    with path.open() as f:
        lines = list(f)
    if not lines:
        raise SchemaVersionMismatch(f"{path}: empty file, no header")
    try:
        header_raw = json.loads(lines[0])
    except json.JSONDecodeError as e:
        raise SchemaVersionMismatch(f"{path}: header line not JSON: {e}") from e
    if "schema_version" not in header_raw:
        raise SchemaVersionMismatch(f"{path}: first line lacks schema_version")
    if header_raw["schema_version"] not in accepted:
        raise SchemaVersionMismatch(
            f"{path}: schema_version={header_raw['schema_version']!r} "
            f"not in expected {sorted(accepted)}"
        )
    header = _dict_to_header(header_raw)
    rows = [json.loads(line) for line in lines[1:] if line.strip()]
    return header, rows


def save_fixtures(path: Path, header: FixtureHeader, rows: Iterable[dict]) -> None:
    """Write header + rows to `path` as JSONL. Overwrites if exists."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        f.write(json.dumps(_header_to_dict(header)) + "\n")
        for row in rows:
            f.write(json.dumps(row) + "\n")
