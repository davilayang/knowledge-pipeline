"""Variant primitives + identity hashing.

`name` is for display; identity for caching and comparison is computed from
`(config, provenance)`. Two variants with the same config + provenance MUST
produce comparable outputs — that's the contract that makes diffs meaningful.
"""

import dataclasses
import hashlib
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from evals.core.types import VariantProvenance


@dataclass(frozen=True)
class Variant:
    name: str
    config: dict
    provenance: VariantProvenance
    run: Callable[[Any], Any]


@dataclass(frozen=True)
class RetrievalVariant:
    name: str
    config: dict
    provenance: VariantProvenance
    setup: Callable[[Any], Any]
    query: Callable[[Any, str], list[str]]


def _stable_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, default=str)


def variant_identity(variant: Variant | RetrievalVariant) -> str:
    """Deterministic sha256 over (config, provenance). Ignores name and callables."""
    payload = {
        "config": variant.config,
        "provenance": dataclasses.asdict(variant.provenance),
    }
    return hashlib.sha256(_stable_json(payload).encode()).hexdigest()


def corpus_signature(content_ids: Sequence[str]) -> str:
    """sha256 over the sorted content-id list. Order-independent; re-anchoring
    naturally invalidates downstream caches keyed on this signature."""
    return hashlib.sha256(json.dumps(sorted(content_ids), sort_keys=True).encode()).hexdigest()
