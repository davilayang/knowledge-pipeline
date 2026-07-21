"""Provenance parity: the retrieval harness persists a RunManifest alongside results."""

import json

from evals.core.manifest import RunManifest
from evals.retrieval.cli import _write_result
from evals.retrieval.types import EvalRunResult


def test_write_result_embeds_manifest(tmp_path):
    result = EvalRunResult(
        embedding_model="text-embedding-3-small",
        embedding_dims=1536,
        chunker_by_source={"raw_store": "markdown"},
    )
    manifest = RunManifest(
        dataset="retrieval_eval.jsonl",
        dataset_schema=1,
        subject="raw_store=markdown",
        subject_model="text-embedding-3-small",
        judge_model=None,
        code_rev="abc123",
        mode="report",
        runs=1,
    )
    out = _write_result(result, manifest=manifest, results_dir=tmp_path)
    payload = json.loads(out.read_text())
    assert payload["manifest"]["dataset"] == "retrieval_eval.jsonl"
    assert payload["manifest"]["subject_model"] == "text-embedding-3-small"
    assert payload["manifest"]["judge_model"] is None
    # result body still present
    assert payload["embedding_model"] == "text-embedding-3-small"
