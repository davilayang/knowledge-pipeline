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


def test_strips_na_footer_from_body(tmp_path: Path):
    # NA appends a provenance/reflection footer after a trailing `---` (a source
    # ref line and/or `**Bold:**` reflection). It's not part of the note's claim
    # text, so it must not leak into the wiki page as prose.
    note = (
        "---\ntitle: OKF\npromote: true\n---\n\n"
        "OKF documents datasets as living markdown files.\n\n"
        "---\n\n"
        "https://knowledge-queue.example/kp_queue::383d (OKF article)\n"
        "**Personal connection:** Planning a POC in BigQuery.\n"
    )
    notes = tmp_path / "notes"
    notes.mkdir()
    (notes / "okf.md").write_text(note)

    body = read_promoted_notes(notes)[0].body

    assert "OKF documents datasets as living markdown files." in body
    assert "Personal connection" not in body
    assert "knowledge-queue.example" not in body


def test_strips_undelimited_source_footer(tmp_path: Path):
    # Some real notes append the footer as a bare trailing `Source: ...` line with
    # NO `---` rule above it. That's still a provenance footer, not claim text —
    # strip it. (A trailing `**Bold:**` with no `---` is left; too ambiguous.)
    note = (
        "---\ntitle: Harness\npromote: true\n---\n\n"
        "A rubric to guide harness design.\n\n"
        "Source: Tejas Kumar on AI Harnesses and Reliable Agents\n"
    )
    notes = tmp_path / "notes"
    notes.mkdir()
    (notes / "harness.md").write_text(note)

    body = read_promoted_notes(notes)[0].body

    assert "A rubric to guide harness design." in body
    assert "Source: Tejas Kumar" not in body


def test_preserves_mid_body_hr_with_real_prose(tmp_path: Path):
    # A `---` horizontal rule followed by real prose is NOT a footer — the
    # trailer strip must leave it (and everything after) intact.
    note = (
        "---\ntitle: X\npromote: true\n---\n\n"
        "First section.\n\n---\n\nSecond section with real content.\n"
    )
    notes = tmp_path / "notes"
    notes.mkdir()
    (notes / "x.md").write_text(note)

    body = read_promoted_notes(notes)[0].body

    assert "Second section with real content." in body


def test_excludes_unpromoted_and_frontmatterless_notes(tmp_path: Path):
    notes = tmp_path / "notes"
    notes.mkdir()
    (notes / "promoted.md").write_text(_PROMOTED)
    (notes / "not-promoted.md").write_text("---\ntitle: X\npromote: false\n---\n\nBody.")
    (notes / "no-frontmatter.md").write_text("Just a plain note, no promote flag.")

    out = read_promoted_notes(notes)

    assert [n.note_id for n in out] == ["promoted"]
