from pathlib import Path

import pytest
from evals.retrieval.dataset import group_by_source, load_eval_set


def _write(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "eval.jsonl"
    p.write_text(body, encoding="utf-8")
    return p


class TestLoadEvalSet:
    def test_parses_well_formed_jsonl(self, tmp_path: Path):
        p = _write(
            tmp_path,
            '{"query": "what is RAG?", "source": "raw_store", "expected_content_id": "abc"}\n'
            '{"query": "session q",   "source": "sessions",  "expected_content_id": "s1"}\n',
        )
        pairs = load_eval_set(p)
        assert len(pairs) == 2
        assert pairs[0].query == "what is RAG?"
        assert pairs[1].source == "sessions"

    def test_skips_blank_lines(self, tmp_path: Path):
        p = _write(
            tmp_path,
            '\n   \n{"query": "q", "source": "notes", "expected_content_id": "n1"}\n\n',
        )
        assert len(load_eval_set(p)) == 1

    def test_rejects_invalid_json(self, tmp_path: Path):
        p = _write(tmp_path, "{not-json\n")
        with pytest.raises(ValueError, match="invalid JSON"):
            load_eval_set(p)

    def test_rejects_missing_keys(self, tmp_path: Path):
        p = _write(tmp_path, '{"query": "q", "source": "notes"}\n')
        with pytest.raises(ValueError, match="missing required key 'expected_content_id'"):
            load_eval_set(p)

    def test_rejects_unknown_source(self, tmp_path: Path):
        p = _write(
            tmp_path,
            '{"query": "q", "source": "wikipedia", "expected_content_id": "x"}\n',
        )
        with pytest.raises(ValueError, match="unknown source"):
            load_eval_set(p)

    def test_accepts_wiki_source(self, tmp_path: Path):
        # Wiki pages are a valid eval source (G1): query → expected entity_id.
        p = _write(
            tmp_path,
            '{"query": "what do I know about context rot?", "source": "wiki", '
            '"expected_content_id": "concept__context_rot"}\n',
        )
        pairs = load_eval_set(p)
        assert pairs[0].source == "wiki"
        assert pairs[0].expected_content_id == "concept__context_rot"


class TestGroupBySource:
    def test_buckets_pairs_per_source(self, tmp_path: Path):
        p = _write(
            tmp_path,
            '{"query": "q1", "source": "raw_store", "expected_content_id": "a"}\n'
            '{"query": "q2", "source": "raw_store", "expected_content_id": "b"}\n'
            '{"query": "q3", "source": "notes",     "expected_content_id": "c"}\n',
        )
        pairs = load_eval_set(p)
        grouped = group_by_source(pairs)
        assert len(grouped["raw_store"]) == 2
        assert len(grouped["notes"]) == 1
        assert grouped["sessions"] == []
        assert grouped["wiki"] == []
