"""JSON-safe snapshot — emits {"__skipped__": "<type>"} sentinels for non-serializable values."""

import json
from dataclasses import dataclass

from evals.core.snapshotter import snapshot


def test_primitives_pass_through():
    out = snapshot({"a": 1, "b": "x", "c": None, "d": True})
    assert out == {"a": 1, "b": "x", "c": None, "d": True}


def test_nested_dicts_recursed():
    out = snapshot({"a": {"b": {"c": 1}}})
    assert out == {"a": {"b": {"c": 1}}}


def test_lists_recursed():
    out = snapshot({"items": [{"x": 1}, {"y": 2}]})
    assert out == {"items": [{"x": 1}, {"y": 2}]}


def test_non_serializable_becomes_sentinel():
    class _Conn:
        pass

    out = snapshot({"db": _Conn(), "ok": 1})
    assert out == {"db": {"__skipped__": "_Conn"}, "ok": 1}


def test_nested_non_serializable_sentinel():
    class _AliasStore:
        pass

    out = snapshot({"meta": {"store": _AliasStore(), "n": 5}})
    assert out == {"meta": {"store": {"__skipped__": "_AliasStore"}, "n": 5}}


def test_snapshot_output_is_json_dumpable():
    """The whole point: snapshot output must round-trip through json.dumps cleanly."""

    class _X:
        pass

    out = snapshot({"k": _X(), "n": [1, _X()]})
    json.dumps(out)  # no raise


def test_dataclass_is_serialized_as_dict():
    @dataclass
    class Point:
        x: int
        y: int

    out = snapshot({"p": Point(1, 2)})
    assert out == {"p": {"x": 1, "y": 2}}
