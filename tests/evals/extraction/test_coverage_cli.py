"""Tests for the eval-narrative-coverage CLI (no OpenAI — dry-run + guard paths)."""

import json

from evals.extraction.coverage_cli import _DEFAULT_GOLD, _make_judge, _repo_root, main


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

    # Floor, not a literal: deriving both sides from the same file means a
    # dataset that silently SHRANK would still match. The floor is the
    # committed size, so losing a fixture fails while adding one does not.
    assert len(fixtures) >= 10
    assert threads >= 206

    rc = main(["--dry-run"])
    assert rc == 0
    out = capsys.readouterr().out
    assert f"{len(fixtures)} fixtures" in out
    assert f"{threads} threads" in out
    assert "DRY RUN" in out


def test_missing_api_key_exits_cleanly(monkeypatch, capsys):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    rc = main(["--narrative", "narrative_v3"])
    assert rc == 2
    assert "OPENAI_API_KEY" in capsys.readouterr().out


def test_judge_sends_reasoning_models_the_token_param_they_accept(monkeypatch):
    """gpt-5 rejects `max_tokens` outright, so a judge that hardcodes it cannot
    score a run against the model production actually uses."""
    sent = {}

    class _Stub:
        def __init__(self, api_key):
            self.chat = self

        @property
        def completions(self):
            return self

        def create(self, **kw):
            sent.update(kw)
            raise AssertionError("stop after capturing kwargs")

    import openai

    monkeypatch.setattr(openai, "OpenAI", _Stub)
    judge = _make_judge("k", "gpt-5-mini")
    try:
        judge("prompt")
    except AssertionError:
        pass

    assert "max_tokens" not in sent
    assert sent["max_completion_tokens"] == 2048


def test_judge_model_defaults_away_from_the_extraction_model(capsys):
    """A judge drawn from the model under test grades its own output. The
    manifest carries `subject_model` and `judge_model` as separate fields; the
    CLI has to be able to make them differ."""
    rc = main(["--model", "gpt-5-mini", "--dry-run"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "judge=gpt-4.1-mini" in out
