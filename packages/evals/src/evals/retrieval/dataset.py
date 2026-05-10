"""JSONL eval-set loader."""

import json
from collections.abc import Iterable
from pathlib import Path

from .types import VALID_SOURCES, EvalPair


def load_eval_set(path: Path) -> list[EvalPair]:
    """Read JSONL eval pairs from ``path``. Empty lines are ignored.

    Raises ``ValueError`` if any row is missing keys or has an unknown
    ``source`` — bad data fails loudly so eval runs don't silently skip rows.
    """
    pairs: list[EvalPair] = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            row = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{lineno}: invalid JSON ({exc.msg})") from exc
        pairs.append(_to_pair(row, path=path, lineno=lineno))
    return pairs


def group_by_source(pairs: Iterable[EvalPair]) -> dict[str, list[EvalPair]]:
    out: dict[str, list[EvalPair]] = {s: [] for s in VALID_SOURCES}
    for p in pairs:
        out[p.source].append(p)
    return out


def _to_pair(row: dict, *, path: Path, lineno: int) -> EvalPair:
    for key in ("query", "source", "expected_content_id"):
        if key not in row:
            raise ValueError(f"{path}:{lineno}: missing required key {key!r}")
    if row["source"] not in VALID_SOURCES:
        raise ValueError(
            f"{path}:{lineno}: unknown source {row['source']!r}; " f"must be one of {VALID_SOURCES}"
        )
    return EvalPair(
        query=row["query"],
        source=row["source"],
        expected_content_id=row["expected_content_id"],
    )
