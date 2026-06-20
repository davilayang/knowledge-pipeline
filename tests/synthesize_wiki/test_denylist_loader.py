"""Fail-closed denylist loader (R2).

load_rejected_entities(reader, snapshot_path) wraps the Notion reader with a
last-known-good JSON snapshot:
  - success  → return the live denylist AND refresh the snapshot
  - failure with snapshot → reuse the snapshot (fail-CLOSED, never empty)
  - failure with no snapshot → empty (bootstrap only)
A Notion outage must never silently re-admit rejected entities, so empty is
reachable only on first run.
"""

import json
from unittest.mock import MagicMock

from orchestrators.defs.synthesize_wiki.denylist import load_rejected_entities


def test_success_returns_data_and_refreshes_snapshot(tmp_path):
    reader = MagicMock()
    reader.query_rejected.return_value = {"concept__cli": {"category": "generic", "reason": "x"}}
    snap = tmp_path / "denylist.json"

    result = load_rejected_entities(reader, snap)

    assert result == {"concept__cli": {"category": "generic", "reason": "x"}}
    assert json.loads(snap.read_text()) == result


def test_failure_reuses_last_known_good_snapshot(tmp_path):
    """Notion error with a snapshot present → reuse it, do NOT fail open."""
    snap = tmp_path / "denylist.json"
    snap.write_text(json.dumps({"tool__cli": {"category": "generic", "reason": "y"}}))
    reader = MagicMock()
    reader.query_rejected.side_effect = RuntimeError("notion unreachable")

    result = load_rejected_entities(reader, snap)

    assert result == {"tool__cli": {"category": "generic", "reason": "y"}}


def test_failure_without_snapshot_returns_empty(tmp_path):
    """Notion error and no snapshot yet → empty (bootstrap only)."""
    snap = tmp_path / "denylist.json"  # absent
    reader = MagicMock()
    reader.query_rejected.side_effect = RuntimeError("notion unreachable")

    assert load_rejected_entities(reader, snap) == {}
