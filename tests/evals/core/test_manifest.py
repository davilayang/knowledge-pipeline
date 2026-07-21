"""Tests for evals.core.manifest — the provenance envelope + display."""

from evals.core.manifest import RunManifest, format_manifest_line


def test_format_manifest_line_is_a_one_line_provenance_summary():
    m = RunManifest(
        dataset="extract_claims_eval.jsonl",
        dataset_schema=1,
        subject="extract_claims",
        subject_model="gpt-4.1-mini",
        judge_model="gpt-4.1",
        code_rev="abc123",
        mode="report",
        runs=1,
    )
    line = format_manifest_line(m)
    assert "\n" not in line
    for token in ("extract_claims_eval.jsonl", "gpt-4.1-mini", "gpt-4.1", "abc123", "report"):
        assert token in line
