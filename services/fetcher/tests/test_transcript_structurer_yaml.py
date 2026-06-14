"""Regression test for transcript_structurer.yaml chain-entry timeouts.

End-to-end smoke against a real LLM found the original 240s/120s
timeouts in transcript_structurer.yaml too tight for full-podcast input
(~22k tokens × 1.0 output ratio = 220-440s minimum streaming time at
40-100 tok/s). Bumped to 600s/600s.

This test pins the shipped values so a future revert doesn't silently
re-introduce the timeout cliff that produced 502s in production.
"""

from pathlib import Path

from fetcher.extractors._cloud_chain import _load_chain


_YAML_PATH = Path(__file__).parent.parent / "config" / "transcript_structurer.yaml"

_MIN_ATTEMPT_TIMEOUT_S = 600.0


def test_every_transcript_structurer_chain_entry_has_at_least_min_timeout() -> None:
    """Each chain entry's attempt_timeout must accommodate ~22k-token transcripts.
    Lower budgets caused the E2E to 502 on real podcast input."""
    entries = _load_chain(_YAML_PATH)
    assert entries, "transcript_structurer.yaml chain is empty — structurer unreachable"
    for entry in entries:
        assert entry.attempt_timeout >= _MIN_ATTEMPT_TIMEOUT_S, (
            f"chain entry {entry.provider}:{entry.model} has attempt_timeout="
            f"{entry.attempt_timeout}s, below the {_MIN_ATTEMPT_TIMEOUT_S}s floor "
            "needed for full-podcast transcripts"
        )
