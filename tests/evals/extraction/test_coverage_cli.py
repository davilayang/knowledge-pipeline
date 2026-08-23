"""Tests for the eval-narrative-coverage CLI (no OpenAI — dry-run + guard paths)."""

import json

from evals.extraction.coverage_cli import _DEFAULT_GOLD, _repo_root, main


def test_dry_run_reads_gold_and_estimates(capsys):
    """Counts are derived from the committed gold, not written as literals:
    widening the dataset is a legitimate change that must not fail this test,
    while reading the wrong file (or none) still must."""
    rows = [
        json.loads(line)
        for line in (_repo_root() / _DEFAULT_GOLD).read_text().splitlines()
        if line.strip()
    ]
    fixtures = [r for r in rows if "gold_threads" in r]
    threads = sum(len(r["gold_threads"]) for r in fixtures)

    rc = main(["--dry-run"])
    assert rc == 0
    out = capsys.readouterr().out
    assert f"{len(fixtures)} fixtures" in out
    assert f"{threads} threads" in out
    assert "DRY RUN" in out


def test_missing_api_key_exits_cleanly(monkeypatch, capsys):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    rc = main(["--narrative", "narrative_v2"])
    assert rc == 2
    assert "OPENAI_API_KEY" in capsys.readouterr().out
