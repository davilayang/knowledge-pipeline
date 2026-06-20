"""Fail-closed loader for the W2.5 entity rejection list.

Wraps the Notion reader (WikiPagesNotionResource.query_rejected) with a
last-known-good JSON snapshot so a Notion outage never silently re-admits
rejected entities:

  - success  → return the live denylist AND atomically refresh the snapshot
  - Notion error with a snapshot → reuse the snapshot, warn (fail-CLOSED)
  - Notion error with no snapshot → empty, warn (bootstrap only)

An empty denylist is reachable only on first run; every later failure falls
back to the last good list, not to "filter nothing".
"""

import json
import logging
import os
from pathlib import Path
from typing import Protocol

logger = logging.getLogger(__name__)

Denylist = dict[str, dict[str, str | None]]


class _RejectedReader(Protocol):
    def query_rejected(self) -> Denylist: ...


def load_rejected_entities(reader: _RejectedReader, snapshot_path: Path) -> Denylist:
    try:
        data = reader.query_rejected()
    except Exception as exc:
        if snapshot_path.exists():
            logger.warning(
                "Notion denylist read failed (%s); reusing last-known-good snapshot %s",
                exc,
                snapshot_path,
            )
            return json.loads(snapshot_path.read_text())
        logger.warning(
            "Notion denylist read failed (%s) and no snapshot at %s; proceeding with an "
            "EMPTY denylist (bootstrap only — no filtering this tick)",
            exc,
            snapshot_path,
        )
        return {}

    # Atomic refresh — tmp + os.replace, mirroring the aliases.json writer.
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = snapshot_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, snapshot_path)
    return data
