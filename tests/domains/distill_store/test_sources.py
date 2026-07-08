"""Tests for domains.distill_store schema + storage.

distill.db is the single source of truth written by the distill_sessions DAG.
Schema aligns to the typed-claim provenance contract (framing §7 / architecture
§5.8 / ADR-018): the `user` claim class, articulation-ladder kinds, and
correction as a lineage relation (refines_id + relation), not a rewrite.

- session_summaries  one summary row per session.
- user_claims      the `user` provenance class — what the user articulated,
                   verbatim. kind is an articulation-ladder rung. An
                   entity-attached claim renders on a wiki page (`## Your take`);
                   a self/project-anchored `transfer` open-loop is read by NA.
- distill_calls    append-only ledger of every LLM call (summarise_session /
                   extract_user_claims) — prompt version, model, tokens, latency.
                   Artifacts point back via `call_id`; historical output is
                   rebuildable by re-running the recorded prompt over the
                   (immutable) transcript, so the output itself isn't stored.
"""

from pathlib import Path

from domains.distill_store.sources import (
    _connect,
    create_schema,
    get_distill_calls,
    get_session_summary,
    get_user_claims,
    insert_distill_call,
    insert_user_claim,
    upsert_session_summary,
)


def test_create_schema_creates_the_three_tables(tmp_path: Path) -> None:
    db_path = tmp_path / "distill.db"
    create_schema(db_path=db_path)

    with _connect(db_path) as conn:
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
    assert tables == {"session_summaries", "user_claims", "distill_calls"}


def test_distill_call_round_trips(tmp_path: Path) -> None:
    db_path = tmp_path / "distill.db"
    create_schema(db_path=db_path)

    call_id = insert_distill_call(
        db_path=db_path,
        session_id="newsletter-abc123",
        call_kind="summarise_session",
        prompt_label="session_summary_v1",
        prompt_sha256="deadbeef",
        model="gpt-5-mini",
        tokens_in=1200,
        tokens_out=180,
        cached_tokens=1024,
        duration_ms=842.0,
    )
    calls = get_distill_calls(db_path=db_path, session_id="newsletter-abc123")

    assert len(calls) == 1
    c = calls[0]
    assert c["id"] == call_id
    assert c["call_kind"] == "summarise_session"
    assert c["prompt_label"] == "session_summary_v1"
    assert c["prompt_sha256"] == "deadbeef"
    assert c["model"] == "gpt-5-mini"
    assert c["tokens_in"] == 1200
    assert c["tokens_out"] == 180
    assert c["cached_tokens"] == 1024
    assert c["duration_ms"] == 842.0
    assert c["generated_at"]  # stamped on write


def test_distill_calls_are_append_only_history(tmp_path: Path) -> None:
    # Re-running the same call_kind on a session appends a new row (no upsert) —
    # the ledger is the rebuildable iteration history.
    db_path = tmp_path / "distill.db"
    create_schema(db_path=db_path)

    for label in ("session_summary_v1", "session_summary_v2"):
        insert_distill_call(
            db_path=db_path,
            session_id="s1",
            call_kind="summarise_session",
            prompt_label=label,
            prompt_sha256=label,
            model="gpt-5-mini",
            tokens_in=100,
            tokens_out=20,
        )
    calls = get_distill_calls(db_path=db_path, session_id="s1", call_kind="summarise_session")

    assert [c["prompt_label"] for c in calls] == ["session_summary_v1", "session_summary_v2"]


def test_session_summary_links_to_its_call(tmp_path: Path) -> None:
    db_path = tmp_path / "distill.db"
    create_schema(db_path=db_path)

    call_id = insert_distill_call(
        db_path=db_path,
        session_id="s1",
        call_kind="summarise_session",
        prompt_label="session_summary_v1",
        prompt_sha256="x",
        model="gpt-5-mini",
        tokens_in=100,
        tokens_out=20,
    )
    upsert_session_summary(
        db_path=db_path,
        session_id="s1",
        session_date="2026-06-10",
        summary="a summary",
        call_id=call_id,
    )
    row = get_session_summary(db_path=db_path, session_id="s1")

    assert row is not None
    assert row["call_id"] == call_id


def test_many_user_claims_share_one_call_id(tmp_path: Path) -> None:
    # extract_user_claims is one LLM call producing N claims — all carry the
    # same call_id (clean token attribution via the ledger, not per-claim).
    db_path = tmp_path / "distill.db"
    create_schema(db_path=db_path)

    call_id = insert_distill_call(
        db_path=db_path,
        session_id="s1",
        call_kind="extract_user_claims",
        prompt_label="user_claims_v1",
        prompt_sha256="y",
        model="gpt-5-mini",
        tokens_in=2000,
        tokens_out=300,
    )
    for kind in ("paraphrase", "objection"):
        insert_user_claim(
            db_path=db_path,
            kind=kind,
            verbatim_user_text=f"a {kind}",
            status="confirmed",
            session_id="s1",
            call_id=call_id,
        )
    rows = get_user_claims(db_path=db_path, session_id="s1")

    assert {r["call_id"] for r in rows} == {call_id}


def test_session_summary_round_trips(tmp_path: Path) -> None:
    db_path = tmp_path / "distill.db"
    create_schema(db_path=db_path)

    upsert_session_summary(
        db_path=db_path,
        session_id="newsletter-abc123",
        session_date="2026-06-10",
        summary="Discussed Benchling's multi-model + HITL agent architecture.",
    )
    row = get_session_summary(db_path=db_path, session_id="newsletter-abc123")

    assert row is not None
    assert row["session_id"] == "newsletter-abc123"
    assert row["session_date"] == "2026-06-10"
    assert row["summary"].startswith("Discussed Benchling")
    assert row["distilled_at"]  # watermark stamped on write


def test_session_summary_upsert_replaces(tmp_path: Path) -> None:
    db_path = tmp_path / "distill.db"
    create_schema(db_path=db_path)

    upsert_session_summary(
        db_path=db_path, session_id="s1", session_date="2026-06-10", summary="first"
    )
    upsert_session_summary(
        db_path=db_path, session_id="s1", session_date="2026-06-10", summary="second"
    )
    row = get_session_summary(db_path=db_path, session_id="s1")

    assert row is not None
    assert row["summary"] == "second"


def test_get_session_summary_missing_returns_none(tmp_path: Path) -> None:
    db_path = tmp_path / "distill.db"
    create_schema(db_path=db_path)

    assert get_session_summary(db_path=db_path, session_id="nope") is None


def test_user_claim_round_trips(tmp_path: Path) -> None:
    db_path = tmp_path / "distill.db"
    create_schema(db_path=db_path)

    insert_user_claim(
        db_path=db_path,
        kind="paraphrase",
        verbatim_user_text="so it's essentially multi model and human in the loop to verify",
        status="confirmed",
        session_id="newsletter-abc123",
        self_ref=False,
        entity_mention="Benchling",
        event_seq_span=[16, 33],
        content_source="kp_queue::380d",
        context_date="2026-06",
    )
    rows = get_user_claims(db_path=db_path, session_id="newsletter-abc123")

    assert len(rows) == 1
    r = rows[0]
    assert r["kind"] == "paraphrase"
    assert r["status"] == "confirmed"
    assert r["self_ref"] is False  # int 0 exposed as bool
    assert r["entity_mention"] == "Benchling"
    assert r["event_seq_span"] == [16, 33]  # JSON text exposed as list[int]
    assert r["verbatim_user_text"].startswith("so it's essentially")


def test_transfer_open_loop_is_a_user_claim(tmp_path: Path) -> None:
    # A self/project-anchored unmet apply-intent is a user_claim of kind=transfer,
    # status=open — not a separate table. entity_mention points at the project.
    db_path = tmp_path / "distill.db"
    create_schema(db_path=db_path)

    insert_user_claim(
        db_path=db_path,
        kind="transfer",
        verbatim_user_text="what is applicable in a data team?",
        status="open",
        session_id="newsletter-abc123",
        self_ref=True,
        entity_mention="Apolitical data-eng team",
        note_ref="note-838",
    )
    r = get_user_claims(db_path=db_path, session_id="newsletter-abc123")[0]

    assert r["kind"] == "transfer"
    assert r["status"] == "open"
    assert r["self_ref"] is True
    assert r["note_ref"] == "note-838"


def test_get_user_claims_scoped_by_session(tmp_path: Path) -> None:
    db_path = tmp_path / "distill.db"
    create_schema(db_path=db_path)

    insert_user_claim(
        db_path=db_path,
        kind="objection",
        verbatim_user_text="or is it just a promotion piece?",
        status="open",
        session_id="s1",
    )
    insert_user_claim(
        db_path=db_path,
        kind="example",
        verbatim_user_text="so it's like integration testing, end to end",
        status="confirmed",
        session_id="s2",
    )

    assert len(get_user_claims(db_path=db_path, session_id="s1")) == 1
    assert get_user_claims(db_path=db_path, session_id="s2")[0]["kind"] == "example"


def test_correction_is_a_lineage_relation(tmp_path: Path) -> None:
    # Correction = a new append-only claim pointing back via refines_id + relation,
    # never a rewrite of the original (ADR-018).
    db_path = tmp_path / "distill.db"
    create_schema(db_path=db_path)

    original = insert_user_claim(
        db_path=db_path,
        kind="paraphrase",
        verbatim_user_text="ontology is just a database schema",
        status="superseded",
        session_id="s1",
    )
    refined = insert_user_claim(
        db_path=db_path,
        kind="paraphrase",
        verbatim_user_text="ontology adds formal axioms a schema lacks",
        status="confirmed",
        session_id="s1",
        refines_id=original,
        relation="supersedes",
    )
    rows = {r["id"]: r for r in get_user_claims(db_path=db_path, session_id="s1")}

    assert rows[original]["verbatim_user_text"] == "ontology is just a database schema"
    assert rows[refined]["refines_id"] == original
    assert rows[refined]["relation"] == "supersedes"
