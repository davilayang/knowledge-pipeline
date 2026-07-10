"""Tests for the frontmatter-aware promoted-notes reader (KP-2 slice 0).

The reader surfaces the fields `LocalFileSource` discards (promote / entities /
updated_at / session_id) and keys a note by its filename stem (NA ships no
`note_id:` field), returning only notes flagged `promote: true`.
"""

from pathlib import Path

from domains.notes.promoted import read_promoted_notes

_PROMOTED = (
    "---\n"
    "title: Agent Harness framework\n"
    "date: '2026-07-08'\n"
    "session_id: newsletter-929dc7346f57\n"
    "entities: [agent harness, HARNESS.md, progressive disclosure]\n"
    "promote: true\n"
    "updated_at: '2026-07-08T16:51:00+00:00'\n"
    "---\n\n"
    "The harness is a progressive-disclosure engine.\n"
)


def test_reads_promoted_note_fields(tmp_path: Path):
    notes = tmp_path / "notes"
    notes.mkdir()
    (notes / "2026-07-08_agent-harness-a1b2c3.md").write_text(_PROMOTED)

    out = read_promoted_notes(notes)

    assert len(out) == 1
    n = out[0]
    assert n.note_id == "2026-07-08_agent-harness-a1b2c3"  # filename stem
    assert n.entities == ("agent harness", "HARNESS.md", "progressive disclosure")  # ordered
    assert n.session_id == "newsletter-929dc7346f57"
    assert n.updated_at == "2026-07-08T16:51:00+00:00"
    assert "progressive-disclosure engine" in n.body


def test_excludes_unpromoted_and_frontmatterless_notes(tmp_path: Path):
    notes = tmp_path / "notes"
    notes.mkdir()
    (notes / "promoted.md").write_text(_PROMOTED)
    (notes / "not-promoted.md").write_text("---\ntitle: X\npromote: false\n---\n\nBody.")
    (notes / "no-frontmatter.md").write_text("Just a plain note, no promote flag.")

    out = read_promoted_notes(notes)

    assert [n.note_id for n in out] == ["promoted"]
