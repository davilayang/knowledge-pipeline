"""Tests for the eval-narrative-coverage CLI (no OpenAI — dry-run + guard paths)."""

from evals.extraction.coverage_cli import main


def test_dry_run_reads_gold_and_estimates(capsys):
    rc = main(["--dry-run"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "7 fixtures" in out  # the committed gold
    assert "137 threads" in out
    assert "DRY RUN" in out


def test_missing_api_key_exits_cleanly(monkeypatch, capsys):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    rc = main(["--narrative", "narrative_v2"])
    assert rc == 2
    assert "OPENAI_API_KEY" in capsys.readouterr().out
