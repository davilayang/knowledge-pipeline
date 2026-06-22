"""Pure-function tests for synthesize_wiki/assets.py helpers.

Full asset execution is covered by the wiki_synthesis suite (synthesize_item).
These cover the pure helpers — _cost_metadata aggregation correctness — plus
the empty-pending shortcut and the Notion denylist injection, where a
regression would silently change a Dagster materialization.
"""

from unittest.mock import MagicMock, patch

import dagster as dg
import pytest
from orchestrators.defs.synthesize_wiki.assets import _cost_metadata, extracted, synthesized
from orchestrators.defs.synthesize_wiki.resources import WikiResource
from workflows.llm import LLMCall


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


# ---------- extracted: per-item candidate map ----------


def test_extracted_builds_candidate_map(tmp_path):
    """wiki/extracted runs extract_item per pending item and emits a per-item
    {candidates, extract_error} map — the artifact wiki/synthesized consumes."""
    wiki = WikiResource(
        backup_dir=str(tmp_path),
        wiki_dir=str(tmp_path / "wiki"),
        wiki_db_path=str(tmp_path / "wiki.db"),
    )
    ctx = MagicMock(spec=dg.AssetExecutionContext)
    ctx.partition_key = "2026-05-07"

    item = MagicMock(item_id="medium::x", source_type="raw_store")
    source = MagicMock()
    source.get_item.return_value = item

    with (
        patch("orchestrators.defs.synthesize_wiki.assets.RawStoreSource", return_value=source),
        patch(
            "orchestrators.defs.synthesize_wiki.assets.extract_item",
            return_value={"candidates": ["c1", "c2"], "extract_error": None, "llm_calls": []},
        ) as mock_extract,
    ):
        out = extracted.op.compute_fn.decorated_fn(ctx, pending=["medium::x"], wiki=wiki)

    mock_extract.assert_called_once()
    assert out.value == {"medium::x": {"candidates": ["c1", "c2"], "extract_error": None}}
    assert out.metadata["candidate_count"].value == 2


def test_extracted_no_op_on_empty_pending(tmp_path):
    wiki = WikiResource(backup_dir=str(tmp_path))
    ctx = MagicMock(spec=dg.AssetExecutionContext)
    ctx.partition_key = "2026-05-07"

    out = extracted.op.compute_fn.decorated_fn(ctx, pending=[], wiki=wiki)
    assert out.value == {}
    assert "_no pending items this tick_" in out.metadata["summary"].value


def test_extracted_raises_on_missing_item(tmp_path):
    """A pending id absent from the snapshot fails the run loudly (run-killing
    error path) rather than silently dropping the item."""
    wiki = WikiResource(
        backup_dir=str(tmp_path),
        wiki_dir=str(tmp_path / "wiki"),
        wiki_db_path=str(tmp_path / "wiki.db"),
    )
    ctx = MagicMock(spec=dg.AssetExecutionContext)
    ctx.partition_key = "2026-05-07"

    source = MagicMock()
    source.get_item.return_value = None  # item vanished from the snapshot

    with (
        patch("orchestrators.defs.synthesize_wiki.assets.RawStoreSource", return_value=source),
        patch("orchestrators.defs.synthesize_wiki.assets.extract_item") as mock_extract,
        pytest.raises(dg.Failure, match="missing 1 item"),
    ):
        extracted.op.compute_fn.decorated_fn(ctx, pending=["medium::gone"], wiki=wiki)

    mock_extract.assert_not_called()  # no extraction attempted for a missing item


# ---------- synthesized: consumes the extracted candidate map ----------


def test_synthesized_no_op_on_empty_extracted(tmp_path):
    """Empty work order: short-circuit with the no-op summary; no LLM calls, no
    DB, no snapshot read."""
    wiki = WikiResource(backup_dir=str(tmp_path))
    ctx = MagicMock(spec=dg.AssetExecutionContext)
    ctx.partition_key = "2026-05-07"

    result = synthesized.op.compute_fn.decorated_fn(
        ctx, extracted={}, wiki=wiki, wiki_pages_notion=MagicMock()
    )

    assert isinstance(result, dg.MaterializeResult)
    assert "_no extracted items this tick_" in result.metadata["summary"].value


def test_synthesized_injects_notion_denylist(tmp_path):
    """The asset loads the rejection list from the Notion 'Wiki Pages' DB and
    injects the rejected names into the workflow per item (W2.5 Notion seam)."""
    wiki = WikiResource(
        backup_dir=str(tmp_path),
        wiki_dir=str(tmp_path / "wiki"),
        wiki_db_path=str(tmp_path / "wiki.db"),
    )
    ctx = MagicMock(spec=dg.AssetExecutionContext)
    ctx.partition_key = "2026-05-07"

    notion = MagicMock()
    notion.query_rejected.return_value = {"cli": {"category": "generic", "reason": "z"}}

    item = MagicMock(item_id="medium::x", source_type="raw_store")
    source = MagicMock()
    source.get_item.return_value = item
    extracted_map = {"medium::x": {"candidates": [], "extract_error": None}}

    with (
        patch(
            "orchestrators.defs.synthesize_wiki.assets.RawStoreSource",
            return_value=source,
        ),
        patch(
            "orchestrators.defs.synthesize_wiki.assets.synthesize_extracted_item",
            return_value={"llm_calls": []},
        ) as mock_synth,
    ):
        synthesized.op.compute_fn.decorated_fn(
            ctx, extracted=extracted_map, wiki=wiki, wiki_pages_notion=notion
        )

    mock_synth.assert_called_once()
    assert mock_synth.call_args.kwargs["rejected_entities"] == frozenset({"cli"})
    # the per-item extraction payload is threaded through positionally
    assert mock_synth.call_args.args[1] == {"candidates": [], "extract_error": None}
