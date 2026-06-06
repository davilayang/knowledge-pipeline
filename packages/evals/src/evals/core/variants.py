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


_ALLOWED_LEAF_TYPES = (str, int, float, bool, type(None))


def _assert_json_hashable(obj: Any, *, path: str) -> None:
    """Walk obj; raise ValueError if anything would break cross-process determinism.

    json.dumps(sort_keys=True) orders dict keys, but values that fall through to
    `default=str` (sets, frozensets, Path, custom objects) are stringified in
    iteration order — and PYTHONHASHSEED randomises that order between Python
    processes. Reject up-front so the cache contract can't drift silently.
    """
    if isinstance(obj, _ALLOWED_LEAF_TYPES):
        return
    if isinstance(obj, dict):
        for k, v in obj.items():
            if not isinstance(k, str):
                raise ValueError(
                    f"variant identity at {path}: dict key {k!r} "
                    f"(type {type(k).__name__}) is not a str. JSON requires string keys."
                )
            _assert_json_hashable(v, path=f"{path}.{k}")
        return
    if isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            _assert_json_hashable(v, path=f"{path}[{i}]")
        return
    raise ValueError(
        f"variant identity at {path}: value of type {type(obj).__name__} is not "
        "JSON-hashable. Allowed: str, int, float, bool, None, dict[str,...], list, tuple. "
        "Normalise sets / Paths / custom objects to lists / strings before "
        "constructing the Variant."
    )


def _stable_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True)


def variant_identity(variant: Variant | RetrievalVariant) -> str:
    """Deterministic sha256 over (config, provenance). Ignores name and callables.

    `config` + `provenance` must contain only JSON-safe primitives, lists, tuples,
    and dicts with str keys — anything else would silently produce different
    hashes across Python processes due to hash-randomised iteration order on
    sets, and ad-hoc str() coercion on Paths / custom objects. Validation
    raises ValueError at construction time rather than letting the cache
    contract drift silently to cache-miss time.
    """
    config = variant.config
    provenance = dataclasses.asdict(variant.provenance)
    _assert_json_hashable(config, path="config")
    _assert_json_hashable(provenance, path="provenance")
    payload = {"config": config, "provenance": provenance}
    return hashlib.sha256(_stable_json(payload).encode()).hexdigest()


def corpus_signature(content_ids: Sequence[str]) -> str:
    """sha256 over the sorted content-id list. Order-independent; re-anchoring
    naturally invalidates downstream caches keyed on this signature."""
    return hashlib.sha256(json.dumps(sorted(content_ids)).encode()).hexdigest()
