"""Tests for POST /v1/extract — the multi-task LLM extraction endpoint."""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from fetcher.app import create_app
from fetcher.extract.tasks import TASKS


@pytest.fixture
def prompts_root(tmp_path: Path) -> Path:
    """A prompts tree holding every default label, with placeholder bodies.

    Hermetic on purpose: pointing tests at the production prompts would make an
    unrelated prompt edit fail them, and none of these assertions is about the
    prompt wording.
    """
    extraction = tmp_path / "prompts" / "extraction"
    extraction.mkdir(parents=True)
    for spec in TASKS.values():
        (extraction / f"{spec.default_prompt_label}.md").write_text(
            f"design notes header\n\n---\nExtract the {spec.name}.\n"
        )
    return tmp_path / "prompts"


def _setup_envs(monkeypatch, tmp_db_path: str, prompts_root: Path) -> None:
    monkeypatch.setenv("FETCHER_DB_PATH", tmp_db_path)
    monkeypatch.setenv("FETCHER_JINA_API_KEY", "x")
    monkeypatch.setenv("FETCHER_SOCKS5_URL", "socks5://x")
    monkeypatch.setenv("FETCHER_LLAMA_PARSE_API_KEY", "x")
    monkeypatch.setenv("FETCHER_EXTRACTION_PROMPTS_ROOT", str(prompts_root))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("EXTRACT_QUEUE_MODEL", "gpt-5-mini")


def test_unknown_task_name_is_rejected_before_any_model_call(
    monkeypatch, tmp_db_path, prompts_root
) -> None:
    _setup_envs(monkeypatch, tmp_db_path, prompts_root)
    app = create_app()
    with TestClient(app) as client:
        response = client.post(
            "/v1/extract",
            json={
                "content": "some article body",
                "content_type": "article",
                "tasks": ["narrative", "horoscope"],
            },
        )

    assert response.status_code == 400
    body = response.json()
    assert body["code"] == "INVALID_TASK"
    assert "horoscope" in body["detail"]


def test_a_task_requested_twice_is_rejected(monkeypatch, tmp_db_path, prompts_root) -> None:
    """Deduplicating silently would bill one call and answer as if two ran."""
    _setup_envs(monkeypatch, tmp_db_path, prompts_root)
    app = create_app()
    with TestClient(app) as client:
        response = client.post(
            "/v1/extract",
            json={
                "content": "some article body",
                "content_type": "article",
                "tasks": ["narrative", {"task": "narrative", "prompt_version": "narrative_v2"}],
            },
        )

    assert response.status_code == 400
    body = response.json()
    assert body["code"] == "DUPLICATE_TASK"
    assert "narrative" in body["detail"]


def test_empty_content_is_rejected(monkeypatch, tmp_db_path, prompts_root) -> None:
    _setup_envs(monkeypatch, tmp_db_path, prompts_root)
    app = create_app()
    with TestClient(app) as client:
        response = client.post(
            "/v1/extract",
            json={"content": "   \n  ", "content_type": "article", "tasks": ["narrative"]},
        )

    assert response.status_code == 400
    assert response.json()["code"] == "BAD_REQUEST"


def test_unknown_prompt_version_is_rejected(monkeypatch, tmp_db_path, prompts_root) -> None:
    """A label is a filename lookup, so it fails closed — and it fails before the
    batch spends anything, not after the first task has already been billed."""
    _setup_envs(monkeypatch, tmp_db_path, prompts_root)
    app = create_app()
    with TestClient(app) as client:
        response = client.post(
            "/v1/extract",
            json={
                "content": "some article body",
                "content_type": "article",
                "tasks": [{"task": "narrative", "prompt_version": "narrative_v99"}],
            },
        )

    assert response.status_code == 400
    body = response.json()
    assert body["code"] == "UNKNOWN_PROMPT_VERSION"
    assert "narrative_v99" in body["detail"]


TOPIC_CARD = {
    "extracted_title": "Vector search on a budget",
    "core_mechanism": "HNSW indexes neighbour lists to answer nearest-neighbour queries.",
    "best_example": "Spotify's Annoy served 100M tracks on one box.",
    "second_example": None,
    "transferable_pattern": "Trade recall for latency by capping graph degree.",
    "main_tension": "Index build cost against query speed.",
    "candidate_tie_backs": [],
}


def _completion(content: str, *, finish_reason: str = "stop", cached: int = 0):
    """One OpenAI chat-completion response, shaped as the SDK returns it."""
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=content, refusal=None),
                finish_reason=finish_reason,
            )
        ],
        usage=SimpleNamespace(
            prompt_tokens=1000,
            completion_tokens=200,
            prompt_tokens_details=SimpleNamespace(cached_tokens=cached),
        ),
    )


def _client_returning(*completions):
    """A stand-in AsyncOpenAI whose calls yield `completions` in order."""
    client = SimpleNamespace()
    client.chat = SimpleNamespace(
        completions=SimpleNamespace(create=AsyncMock(side_effect=list(completions)))
    )
    client.close = AsyncMock()
    return client


def test_a_successful_task_returns_its_typed_payload(
    monkeypatch, tmp_db_path, prompts_root
) -> None:
    _setup_envs(monkeypatch, tmp_db_path, prompts_root)
    app = create_app()
    client = _client_returning(_completion(json.dumps(TOPIC_CARD)))
    with patch("fetcher.extract.openai_lane.AsyncOpenAI", return_value=client):
        with TestClient(app) as http:
            response = http.post(
                "/v1/extract",
                json={
                    "content": "a long enough article body",
                    "content_type": "article",
                    "tasks": ["topic_card"],
                },
            )

    assert response.status_code == 200
    body = response.json()
    assert body["errors"] == []
    assert [r["task"] for r in body["results"]] == ["topic_card"]
    assert body["results"][0]["payload"]["extracted_title"] == "Vector search on a budget"


def test_one_failing_task_still_returns_the_others(monkeypatch, tmp_db_path, prompts_root) -> None:
    """The deliberate break from the old extractor, which was fail-fast.

    A caller asking for two outputs would rather have one plus a named error
    than a batch that spent its money and returned nothing.
    """
    _setup_envs(monkeypatch, tmp_db_path, prompts_root)
    app = create_app()
    # topic_card runs first, and fails every attempt; followups then succeeds.
    client = _client_returning(
        _completion("not json at all"),
        _completion("not json at all"),
        _completion("not json at all"),
        _completion(json.dumps({"questions": ["a?", "b?", "c?", "d?"], "reader_threads": []})),
    )
    with patch("fetcher.extract.openai_lane.AsyncOpenAI", return_value=client):
        with TestClient(app) as http:
            response = http.post(
                "/v1/extract",
                json={
                    "content": "a long enough article body",
                    "content_type": "article",
                    "tasks": ["followups", "topic_card"],
                },
            )

    assert response.status_code == 200
    body = response.json()
    assert [r["task"] for r in body["results"]] == ["followups"]
    assert [e["task"] for e in body["errors"]] == ["topic_card"]
    assert body["errors"][0]["retryable"] is False
    # Every task reports what it cost, including the one that produced nothing.
    assert {c["task"] for c in body["calls"]} == {"topic_card", "followups"}


def test_tasks_run_in_lane_order_not_request_order(monkeypatch, tmp_db_path, prompts_root) -> None:
    """Order is the service's to choose, not the caller's.

    Every task sends the same article prefix, so the first to run pays for the
    prompt-cache write and the rest read it. A caller that reordered them would
    change nothing about the result and everything about the bill.
    """
    _setup_envs(monkeypatch, tmp_db_path, prompts_root)
    app = create_app()
    client = _client_returning(
        _completion(json.dumps(TOPIC_CARD)),
        _completion(json.dumps({"questions": ["a?", "b?", "c?", "d?"], "reader_threads": []})),
    )
    with patch("fetcher.extract.openai_lane.AsyncOpenAI", return_value=client):
        with TestClient(app) as http:
            response = http.post(
                "/v1/extract",
                json={
                    "content": "a long enough article body",
                    "content_type": "article",
                    "tasks": ["followups", "topic_card"],
                },
            )

    assert response.status_code == 200
    assert [c["task"] for c in response.json()["calls"]] == ["topic_card", "followups"]


def _post_topic_card(http, **overrides):
    body = {
        "content": "a long enough article body",
        "content_type": "article",
        "tasks": ["topic_card"],
    }
    body.update(overrides)
    return http.post("/v1/extract", json=body)


def test_an_identical_repeat_request_is_served_from_cache(
    monkeypatch, tmp_db_path, prompts_root
) -> None:
    _setup_envs(monkeypatch, tmp_db_path, prompts_root)
    app = create_app()
    client = _client_returning(_completion(json.dumps(TOPIC_CARD)))
    with patch("fetcher.extract.openai_lane.AsyncOpenAI", return_value=client):
        with TestClient(app) as http:
            first = _post_topic_card(http)
            second = _post_topic_card(http)

    assert first.status_code == 200
    assert second.status_code == 200
    # One model call served both requests; a second would have raised StopIteration.
    assert client.chat.completions.create.await_count == 1
    assert second.json()["cache_hits"] == ["topic_card"]
    assert second.json()["results"] == first.json()["results"]
    # A hit still reports honest provenance rather than a blank ledger row.
    assert second.json()["calls"][0]["prompt_sha256"] == first.json()["calls"][0]["prompt_sha256"]


@pytest.mark.parametrize(
    "changed",
    [
        pytest.param({"content": "a different article body"}, id="content"),
        pytest.param({"content_type": "youtube"}, id="content_type"),
        pytest.param({"user_notes": "I care about the latency claim"}, id="user_notes"),
        pytest.param({"model": "gpt-4.1-mini"}, id="model"),
        pytest.param(
            {"tasks": [{"task": "topic_card", "prompt_version": "topic_card_v2"}]},
            id="prompt_version",
        ),
    ],
)
def test_changing_any_input_that_moves_the_output_misses_the_cache(
    monkeypatch, tmp_db_path, prompts_root, changed
) -> None:
    """The known failure on this service is an under-specified cache key: the
    structurer's once omitted its hint context and served hits from before those
    hints changed. Each input below changes what the model is shown or which
    model is shown it, so each must produce a different key."""
    (prompts_root / "extraction" / "topic_card_v2.md").write_text("Extract the topic card, v2.\n")
    _setup_envs(monkeypatch, tmp_db_path, prompts_root)
    app = create_app()
    client = _client_returning(
        _completion(json.dumps(TOPIC_CARD)), _completion(json.dumps(TOPIC_CARD))
    )
    with patch("fetcher.extract.openai_lane.AsyncOpenAI", return_value=client):
        with TestClient(app) as http:
            assert _post_topic_card(http).status_code == 200
            second = _post_topic_card(http, **changed)

    assert second.status_code == 200
    assert second.json()["cache_hits"] == []
    assert client.chat.completions.create.await_count == 2


def test_reader_notes_reach_followups_only(monkeypatch, tmp_db_path, prompts_root) -> None:
    """The notes are the reader's, not the source's, and only the followups task
    is asked to turn them into threads. Sending them to every task would also
    put per-item data in the prefix every task shares, costing the cache."""
    _setup_envs(monkeypatch, tmp_db_path, prompts_root)
    app = create_app()
    client = _client_returning(
        _completion(json.dumps(TOPIC_CARD)),
        _completion(json.dumps({"questions": ["a?", "b?", "c?", "d?"], "reader_threads": []})),
    )
    with patch("fetcher.extract.openai_lane.AsyncOpenAI", return_value=client):
        with TestClient(app) as http:
            response = http.post(
                "/v1/extract",
                json={
                    "content": "a long enough article body",
                    "content_type": "article",
                    "user_notes": "I care about the latency claim",
                    "tasks": ["topic_card", "followups"],
                },
            )

    assert response.status_code == 200
    sent = [call.kwargs["messages"] for call in client.chat.completions.create.await_args_list]
    topic_card_messages, followups_messages = sent
    assert "latency claim" not in json.dumps(topic_card_messages)
    assert "latency claim" in followups_messages[-1]["content"]
    # The two messages ahead of the tail are what OpenAI matches on; a note that
    # leaked into either would cost the article cache on every sibling task.
    assert topic_card_messages[:2] == followups_messages[:2]


def test_the_cache_key_covers_the_generation_parameters() -> None:
    """A ceiling or reasoning-effort change alters what the model produces, so
    results banked under the old settings must not keep being served. Nothing on
    the request carries these — they are service constants — so they would be
    invisible to a key built from request fields alone."""
    from fetcher.extract.cache import cache_key

    common = dict(
        task="topic_card",
        content="body",
        content_type="article",
        user_notes=None,
        prompt_sha256="abc",
        provider="openai",
        model="gpt-5-mini",
    )
    assert cache_key(**common, generation={"max_completion_tokens": 4096}) != cache_key(
        **common, generation={"max_completion_tokens": 8192}
    )


def test_prompts_endpoint_reports_the_active_label_and_sha(
    monkeypatch, tmp_db_path, prompts_root
) -> None:
    """A caller that gates on freshness has to know what would be sent before it
    decides whether to send it. The alternative is re-deriving the sha, which
    means re-implementing the system message, envelope and schema block on the
    caller side — the duplication this service exists to remove."""
    _setup_envs(monkeypatch, tmp_db_path, prompts_root)
    app = create_app()
    with TestClient(app) as http:
        response = http.get("/v1/extract/prompts")

    assert response.status_code == 200
    body = response.json()
    # The model is part of the answer: a caller comparing freshness has to know
    # which model would run, and it is the service that decides.
    assert body["model"] == "gpt-5-mini"
    by_task = {p["task"]: p for p in body["prompts"]}
    assert set(by_task) == set(TASKS)
    assert by_task["metadata"]["prompt_label"] == "metadata_v1"
    assert len(by_task["metadata"]["prompt_sha256"]) == 64
    # The sha a run would record, so a caller can compare without guessing.
    assert by_task["topic_card"]["prompt_sha256"] != by_task["followups"]["prompt_sha256"]
