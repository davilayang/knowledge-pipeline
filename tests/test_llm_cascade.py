"""Tests for the shared sync LLM cascade helper used by orchestrator-side
classifiers (`workflows.llm_cascade.run_cascade`).

The helper owns the "try a list of OpenAI-compat chat-completion endpoints
in order, fall through on tier failure, never raise" shape. The first
caller is `ContentShapeClassifier`; the transcript-structurer Groq swap is
queued next. The fetcher service has its own async cascade
(`services/fetcher/extractors/_cloud_chain.py`) and is intentionally not
unified with this — different concurrency model, different failure
contract.
"""

import json
from unittest.mock import MagicMock, patch

import httpx


def _chat_response(json_payload: dict) -> MagicMock:
    """Fake httpx.Response for an OpenAI-compat chat completion."""
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {
        "choices": [{"message": {"content": json.dumps(json_payload)}}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 10},
    }
    return response


def test_no_tiers_returns_skipped_status() -> None:
    """Empty tier list → cascade short-circuits without touching httpx, so a
    caller that hasn't configured any LLM key (e.g. dev box without
    GROQ_API_KEY / OPENAI_API_KEY) gets a structured "skipped" signal it
    can surface in asset metadata. Distinct status from "invalid_output"
    so post-deploy we can tell unconfigured deploys apart from cascade
    exhaustion."""
    from workflows.llm_cascade import CascadeResult, run_cascade

    result = run_cascade(
        tiers=[],
        system_prompt="anything",
        user_prompt="anything",
        validate=lambda payload: (None, None),
    )

    assert result == CascadeResult(value=None, status="skipped_no_tiers", model=None)


def test_single_tier_ok() -> None:
    """Happy path with one tier: cascade calls the tier, parses JSON,
    hands the dict to `validate`, and returns the value the validator
    extracted alongside model + "ok" status. Confirms the cascade
    delegates schema knowledge entirely to the validator — it only
    cares about the (value, sentinel) shape coming back."""
    from workflows.llm_cascade import CascadeResult, CascadeTier, run_cascade

    tier = CascadeTier(model="m1", endpoint="https://api.example.com/v1/chat", api_key="k1")

    with patch(
        "workflows.llm_cascade.httpx.post",
        return_value=_chat_response({"label": "tutorial"}),
    ) as mock_post:
        result = run_cascade(
            tiers=[tier],
            system_prompt="sys",
            user_prompt="usr",
            validate=lambda payload: (payload["label"], None),
        )

    assert result == CascadeResult(value="tutorial", status="ok", model="m1")
    assert mock_post.call_count == 1


def test_sentinel_status_stops_cascade() -> None:
    """When the validator returns a non-None sentinel (e.g. the LLM
    legitimately answered "unknown" within the allowed enum), the cascade
    surfaces that sentinel as `status` and DOES NOT fall through to the
    next tier — the model spoke; trust the answer. Lets the caller tell
    honest abstention apart from exception fall-through in metadata."""
    from workflows.llm_cascade import CascadeResult, CascadeTier, run_cascade

    tier_a = CascadeTier(model="m1", endpoint="https://api.a.example/v1/chat", api_key="k1")
    tier_b = CascadeTier(model="m2", endpoint="https://api.b.example/v1/chat", api_key="k2")

    with patch(
        "workflows.llm_cascade.httpx.post",
        return_value=_chat_response({"label": "unknown"}),
    ) as mock_post:
        result = run_cascade(
            tiers=[tier_a, tier_b],
            system_prompt="sys",
            user_prompt="usr",
            validate=lambda payload: (payload["label"], "returned_unknown"),
        )

    assert result == CascadeResult(value="unknown", status="returned_unknown", model="m1")
    assert mock_post.call_count == 1  # tier B never called


def test_invalid_output_falls_through() -> None:
    """Tier 1 emits a JSON shape the validator can't route on (returns
    (None, None) — neither a value nor a sentinel). Cascade treats it as
    a tier-skip and tries tier 2. Caller benefits from this when a model
    drifts onto a new enum value or hallucinates a key: a different model
    might still answer correctly."""
    from workflows.llm_cascade import CascadeResult, CascadeTier, run_cascade

    tier_a = CascadeTier(model="m1", endpoint="https://api.a.example/v1/chat", api_key="k1")
    tier_b = CascadeTier(model="m2", endpoint="https://api.b.example/v1/chat", api_key="k2")

    with patch(
        "workflows.llm_cascade.httpx.post",
        side_effect=[
            _chat_response({"label": "garbage"}),
            _chat_response({"label": "opinion_essay"}),
        ],
    ) as mock_post:
        result = run_cascade(
            tiers=[tier_a, tier_b],
            system_prompt="sys",
            user_prompt="usr",
            validate=lambda payload: (
                (payload["label"], None) if payload["label"] in {"opinion_essay"} else (None, None)
            ),
        )

    assert result == CascadeResult(value="opinion_essay", status="ok", model="m2")
    assert mock_post.call_count == 2


def test_exception_falls_through() -> None:
    """Network blip / malformed payload on tier 1 → cascade catches and
    moves on rather than propagating. Failing one tier mustn't fail the
    whole call — that defeats the point of having a fallback. Same
    success-shape as `test_invalid_output_falls_through`; the cascade
    treats validator-rejection and exception identically."""
    from workflows.llm_cascade import CascadeResult, CascadeTier, run_cascade

    tier_a = CascadeTier(model="m1", endpoint="https://api.a.example/v1/chat", api_key="k1")
    tier_b = CascadeTier(model="m2", endpoint="https://api.b.example/v1/chat", api_key="k2")

    with patch(
        "workflows.llm_cascade.httpx.post",
        side_effect=[
            httpx.ConnectTimeout("tier a dropped"),
            _chat_response({"label": "tutorial"}),
        ],
    ) as mock_post:
        result = run_cascade(
            tiers=[tier_a, tier_b],
            system_prompt="sys",
            user_prompt="usr",
            validate=lambda payload: (payload["label"], None),
        )

    assert result == CascadeResult(value="tutorial", status="ok", model="m2")
    assert mock_post.call_count == 2


def test_all_tiers_fail_returns_invalid_output() -> None:
    """Every tier exhausted with no success → cascade returns a structured
    failure marker rather than raising. Caller decides how to surface
    that (the content_shape classifier maps it to SHAPE_UNKNOWN +
    triage proceeds). Status distinguishes total failure from
    "skipped_no_tiers" so post-deploy metadata can tell those modes
    apart."""
    from workflows.llm_cascade import CascadeResult, CascadeTier, run_cascade

    tier_a = CascadeTier(model="m1", endpoint="https://api.a.example/v1/chat", api_key="k1")
    tier_b = CascadeTier(model="m2", endpoint="https://api.b.example/v1/chat", api_key="k2")

    with patch(
        "workflows.llm_cascade.httpx.post",
        side_effect=[
            httpx.ConnectTimeout("tier a down"),
            httpx.ReadTimeout("tier b slow"),
        ],
    ) as mock_post:
        result = run_cascade(
            tiers=[tier_a, tier_b],
            system_prompt="sys",
            user_prompt="usr",
            validate=lambda payload: (payload["label"], None),
        )

    assert result == CascadeResult(value=None, status="invalid_output", model=None)
    assert mock_post.call_count == 2


def test_request_payload_shape() -> None:
    """The cascade speaks the OpenAI-compat chat-completions wire format —
    that's why it works for both Groq and OpenAI without per-tier
    serialisation logic. This test pins the request shape so a future
    refactor can't silently drop `response_format` (which forces JSON
    mode) or rename `max_completion_tokens` (which Groq's reasoning
    models depend on)."""
    from workflows.llm_cascade import CascadeTier, run_cascade

    tier = CascadeTier(model="m1", endpoint="https://api.example.com/v1/chat", api_key="secret")

    with patch(
        "workflows.llm_cascade.httpx.post",
        return_value=_chat_response({"label": "ok"}),
    ) as mock_post:
        run_cascade(
            tiers=[tier],
            system_prompt="SYS",
            user_prompt="USR",
            validate=lambda payload: (payload["label"], None),
            timeout_s=12.5,
            max_completion_tokens=500,
            temperature=0.2,
        )

    call = mock_post.call_args
    assert call.args[0] == "https://api.example.com/v1/chat"
    assert call.kwargs["headers"]["Authorization"] == "Bearer secret"
    body = call.kwargs["json"]
    assert body["model"] == "m1"
    assert body["messages"] == [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "USR"},
    ]
    assert body["response_format"] == {"type": "json_object"}
    assert body["temperature"] == 0.2
    assert body["max_completion_tokens"] == 500
    assert call.kwargs["timeout"] == 12.5
