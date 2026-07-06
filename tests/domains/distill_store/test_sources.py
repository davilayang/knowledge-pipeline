"""Tests for domains.distill_store schema + storage.

distill.db is the source of truth for distilled session memory (v1 SPEC):
session_digest (one summary per session), user_generation (the user's own
paraphrases / judgments / connections / open questions), and
project_open_loops (self/project-anchored unresolved apply-intents).
"""

from pathlib import Path

from domains.distill_store.sources import (
    _connect,
    create_schema,
    get_project_open_loops,
    get_session_digest,
    get_user_generations,
    insert_project_open_loop,
    insert_user_generation,
    upsert_session_digest,
)


def test_create_schema_creates_three_tables(tmp_path: Path) -> None:
    db_path = tmp_path / "distill.db"
    create_schema(db_path=db_path)

    with _connect(db_path) as conn:
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
    assert tables == {"session_digest", "user_generation", "project_open_loops"}


def test_session_digest_round_trips(tmp_path: Path) -> None:
    db_path = tmp_path / "distill.db"
    create_schema(db_path=db_path)

    upsert_session_digest(
        db_path=db_path,
        session_id="newsletter-abc123",
        session_date="2026-06-10",
        summary="Discussed Benchling's multi-model + HITL agent architecture.",
    )
    row = get_session_digest(db_path=db_path, session_id="newsletter-abc123")

    assert row is not None
    assert row["session_id"] == "newsletter-abc123"
    assert row["session_date"] == "2026-06-10"
    assert row["summary"].startswith("Discussed Benchling")
    assert row["distilled_at"]  # watermark stamped on write


def test_session_digest_upsert_replaces_summary(tmp_path: Path) -> None:
    db_path = tmp_path / "distill.db"
    create_schema(db_path=db_path)

    upsert_session_digest(
        db_path=db_path, session_id="s1", session_date="2026-06-10", summary="first"
    )
    upsert_session_digest(
        db_path=db_path, session_id="s1", session_date="2026-06-10", summary="second"
    )
    row = get_session_digest(db_path=db_path, session_id="s1")

    assert row is not None
    assert row["summary"] == "second"


def test_get_session_digest_missing_returns_none(tmp_path: Path) -> None:
    db_path = tmp_path / "distill.db"
    create_schema(db_path=db_path)

    assert get_session_digest(db_path=db_path, session_id="nope") is None


def test_user_generation_round_trips(tmp_path: Path) -> None:
    db_path = tmp_path / "distill.db"
    create_schema(db_path=db_path)

    insert_user_generation(
        db_path=db_path,
        kind="paraphrase_check",
        verbatim_user_text="so it's essentially multi model and human in the loop to verify",
        status="confirmed",
        session_id="newsletter-abc123",
        self_ref=False,
        entity_mention="Benchling",
        event_seq_span=[16, 33],
        content_source="kp_queue::380d",
        context_date="2026-06",
    )
    rows = get_user_generations(db_path=db_path, session_id="newsletter-abc123")

    assert len(rows) == 1
    r = rows[0]
    assert r["kind"] == "paraphrase_check"
    assert r["status"] == "confirmed"
    assert r["self_ref"] is False  # int 0 exposed as bool
    assert r["entity_mention"] == "Benchling"
    assert r["event_seq_span"] == [16, 33]  # JSON text exposed as list[int]
    assert r["verbatim_user_text"].startswith("so it's essentially")


def test_get_user_generations_scoped_by_session(tmp_path: Path) -> None:
    db_path = tmp_path / "distill.db"
    create_schema(db_path=db_path)

    insert_user_generation(
        db_path=db_path,
        kind="judgment",
        verbatim_user_text="or is it just a promotion piece?",
        status="open",
        session_id="s1",
    )
    insert_user_generation(
        db_path=db_path,
        kind="claim",
        verbatim_user_text="ontology is just a database schema",
        status="confirmed",
        session_id="s2",
    )

    assert len(get_user_generations(db_path=db_path, session_id="s1")) == 1
    assert get_user_generations(db_path=db_path, session_id="s2")[0]["kind"] == "claim"


def test_project_open_loop_round_trips(tmp_path: Path) -> None:
    db_path = tmp_path / "distill.db"
    create_schema(db_path=db_path)

    insert_project_open_loop(
        db_path=db_path,
        verbatim_user_text="so how to use tool like claude code from this view?",
        loop=(
            "Wants a hands-on way to apply Wes McKinney's agentic-eng discipline "
            "to their Claude Code workflow; unmet in-session, saved a note."
        ),
        status="open",
        session_id="newsletter-abc123",
        self_ref=True,
        entity_mention="Wes McKinney / agentic engineering",
        event_seq_span=[79, 81],
        context_date="2026-06",
        note_ref="note-838",
    )
    rows = get_project_open_loops(db_path=db_path, session_id="newsletter-abc123")

    assert len(rows) == 1
    r = rows[0]
    assert r["status"] == "open"
    assert r["self_ref"] is True
    assert r["event_seq_span"] == [79, 81]
    assert r["note_ref"] == "note-838"
    assert r["loop"].startswith("Wants a hands-on way")
