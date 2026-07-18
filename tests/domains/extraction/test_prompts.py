"""Tests for domains.extraction.prompts.strip_design_notes — header/body split."""

from domains.extraction.prompts import strip_design_notes


def test_returns_body_below_first_horizontal_rule():
    text = "# design notes\nwhat changed and why\n\n---\n\nYou are a helpful extractor.\nRule 1."
    assert strip_design_notes(text) == "You are a helpful extractor.\nRule 1."


def test_passthrough_when_no_separator():
    """A body-only file (no `---`) is returned unchanged."""
    text = "You are a helpful extractor.\nRule 1."
    assert strip_design_notes(text) == text


def test_only_first_rule_splits_body_may_contain_more():
    text = "notes\n---\nbody line\n---\nmore body"
    assert strip_design_notes(text) == "body line\n---\nmore body"
