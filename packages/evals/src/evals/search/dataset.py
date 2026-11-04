"""Loaders for the search probes and gold (`packages/evals/datasets/search_*.jsonl`)."""

from dataclasses import dataclass
from pathlib import Path

from evals.core.fixtures import load_fixtures

SCHEMA_VERSION = 1


@dataclass(frozen=True)
class Probe:
    probe_id: int
    text: str
    caller: str
    type: str
    negative: str | None


@dataclass(frozen=True)
class GoldNeed:
    """One information need: every uri in `uris` is an accepted answer, and a run
    is credited once per need however many of them it returns."""

    gold_id: str
    probe_id: int
    grade: int
    primary: bool
    counter: bool
    label: str
    uris: tuple[str, ...]


def load_probes(path: Path) -> tuple[dict, list[Probe]]:
    header, rows = load_fixtures(path, expected_schema_version=SCHEMA_VERSION)
    return header.extra, [Probe(**r) for r in rows]


def load_gold(path: Path) -> tuple[dict, list[GoldNeed]]:
    header, rows = load_fixtures(path, expected_schema_version=SCHEMA_VERSION)
    return header.extra, [GoldNeed(**{**r, "uris": tuple(r["uris"])}) for r in rows]
