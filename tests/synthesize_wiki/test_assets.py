"""Pure-function tests for synthesize_wiki/assets.py helpers.

The asset functions themselves require a wiki_pg fixture + raw_store.db
file + LangGraph mocking, which is the territory of the existing
wiki_synthesis tests. These tests cover the small standalone helpers:
_resolve_snapshot's Failure paths, _check_snapshot_freshness, and
_cost_metadata's aggregation correctness — places where a regression
would silently change a Dagster materialization's metadata.
"""

from datetime import date, timedelta
from pathlib import Path

import dagster as dg
import pytest
from orchestrators.defs.pipelines.synthesize_wiki.assets import (
    _check_snapshot_freshness,
    _cost_metadata,
    _resolve_snapshot,
)
from orchestrators.defs.pipelines.synthesize_wiki.def_config import MAX_SNAPSHOT_AGE_DAYS
from orchestrators.defs.pipelines.synthesize_wiki.resources import WikiResource
from workflows.llm import LLMCall

# ---------- _resolve_snapshot / _check_snapshot_freshness ----------


def _wiki(tmp_path: Path) -> WikiResource:
    return WikiResource(backup_dir=str(tmp_path), database_url="postgresql://x")


def test_resolve_snapshot_raises_failure_when_no_snapshot(tmp_path: Path):
    """Manual launch with empty backup_dir should fail with backup_dir in the message."""
    with pytest.raises(dg.Failure) as exc:
        _resolve_snapshot(_wiki(tmp_path))
    assert str(tmp_path) in exc.value.description


def test_resolve_snapshot_raises_failure_when_stale(tmp_path: Path):
    """Stale snapshot: dg.Failure with snapshot_path / snapshot_date / age_days metadata."""
    stale_date = date.today() - timedelta(days=MAX_SNAPSHOT_AGE_DAYS + 1)
    stale_dir = tmp_path / stale_date.isoformat()
    stale_dir.mkdir()
    (stale_dir / "raw_store.db").write_text("")

    with pytest.raises(dg.Failure) as exc:
        _resolve_snapshot(_wiki(tmp_path))

    md = exc.value.metadata
    assert md["snapshot_date"].text == stale_date.isoformat()
    assert md["age_days"].value == MAX_SNAPSHOT_AGE_DAYS + 1


def test_check_snapshot_freshness_passes_when_within_limit():
    fresh = date.today() - timedelta(days=MAX_SNAPSHOT_AGE_DAYS)
    _check_snapshot_freshness(Path("/tmp/x.db"), fresh)  # no raise


def test_check_snapshot_freshness_raises_one_day_past_limit():
    stale = date.today() - timedelta(days=MAX_SNAPSHOT_AGE_DAYS + 1)
    with pytest.raises(dg.Failure):
        _check_snapshot_freshness(Path("/tmp/x.db"), stale)


# ---------- _cost_metadata ----------


def _call(model: str, input_tokens: int, output_tokens: int) -> LLMCall:
    return LLMCall(content="", model=model, input_tokens=input_tokens, output_tokens=output_tokens)


def test_cost_metadata_aggregates_by_model():
    calls = [
        _call("gpt-4.1-mini", 1_000, 500),
        _call("gpt-4.1-mini", 2_000, 1_000),
        _call("gpt-4.1-nano", 500, 200),
    ]
    md = _cost_metadata(calls)

    assert md["llm_calls"].value == 3
    assert md["input_tokens"].value == 3_500
    assert md["output_tokens"].value == 1_700
    by_model = md["cost_by_model"].value
    assert set(by_model.keys()) == {"gpt-4.1-mini", "gpt-4.1-nano"}
    assert by_model["gpt-4.1-mini"]["calls"] == 2
    assert by_model["gpt-4.1-mini"]["input_tokens"] == 3_000
    assert by_model["gpt-4.1-nano"]["calls"] == 1


def test_cost_metadata_surfaces_unknown_model():
    """Unknown model: zero cost contribution + name surfaced in unknown_pricing_models."""
    calls = [
        _call("gpt-4.1-mini", 1_000, 0),
        _call("gpt-99-fake", 1_000_000, 1_000_000),
    ]
    md = _cost_metadata(calls)
    assert "unknown_pricing_models" in md
    assert md["unknown_pricing_models"].value == ["gpt-99-fake"]
    # The unknown model's million tokens contribute 0; cost is from gpt-4.1-mini only.
    assert md["cost_usd"].value == round(1_000 * 0.40 / 1_000_000, 4)


def test_cost_metadata_omits_unknown_key_when_all_known():
    """Don't emit unknown_pricing_models when there's nothing to flag."""
    md = _cost_metadata([_call("gpt-4.1-mini", 100, 100)])
    assert "unknown_pricing_models" not in md


def test_cost_metadata_empty_calls():
    """Zero-call case (no items processed) returns zeros, not crashes."""
    md = _cost_metadata([])
    assert md["llm_calls"].value == 0
    assert md["input_tokens"].value == 0
    assert md["cost_usd"].value == 0.0
    assert md["cost_by_model"].value == {}
