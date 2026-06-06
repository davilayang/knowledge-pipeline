"""LangGraph-state-friendly JSON-safe snapshot.

A snapshot is the input dict filtered to JSON-serializable fields only.
Non-serializable values (DB connections, AliasStore objects, large in-memory
buffers) are replaced with `{"__skipped__": "<type-name>"}`. Workbench is
the only consumer; production code is unaware of this module.

Implementer-level rule when adding a new field to WikiSynthesisState:
decide whether it's traceable (JSON-able) or sentinel-only; document in the
node's docstring. The default behaviour falls back to sentinel for unknown
types, so forgetting to document doesn't break the workbench — it just
produces a less useful trace for that field.
"""

import dataclasses
import json
from typing import Any

_JSON_PRIMITIVES = (str, int, float, bool, type(None))


def _is_json_primitive(value: Any) -> bool:
    return isinstance(value, _JSON_PRIMITIVES)


def _sentinel(value: Any) -> dict:
    return {"__skipped__": type(value).__name__}


def snapshot(value: Any) -> Any:
    """Return a JSON-serializable copy of `value`.

    Recursively walks dicts and lists; replaces anything non-serializable with
    a `{"__skipped__": "<type-name>"}` sentinel.
    """
    if _is_json_primitive(value):
        return value
    if isinstance(value, dict):
        return {str(k): snapshot(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [snapshot(v) for v in value]
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return snapshot(dataclasses.asdict(value))
    # Last resort: try json.dumps to detect serializability cheaply.
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return _sentinel(value)
