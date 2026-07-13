"""Tests for the LLM-primary content_shape classifier resource.

The `ContentShapeClassifier` Dagster resource owns the 2-tier Groq → OpenAI
cascade that picks one of `ALL_CONTENT_SHAPES` from an enrichment payload.
Never raises — failure modes (no key configured, exception, invalid output,
all tiers fail) all return `SHAPE_UNKNOWN` so the triage asset can land the
page even when the LLM is unavailable.
"""

import json
from unittest.mock import MagicMock, patch

import httpx
from orchestrators.defs.triage_knowledge_queue.content_shape import SHAPE_UNKNOWN
from orchestrators.defs.triage_knowledge_queue.content_shape_llm import (
    ContentShapeClassifier,
)
from orchestrators.defs.triage_knowledge_queue.enrich import (
    ArticleSignals,
    EnrichmentSignals,
)


def _mock_chat_response(content_shape: str, status_code: int = 200) -> MagicMock:
    """Build a fake httpx.Response for an OpenAI-compat chat completion."""
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = {
        "choices": [{"message": {"content": json.dumps({"content_shape": content_shape})}}],
        "usage": {"prompt_tokens": 200, "completion_tokens": 12},
    }
    response.text = json.dumps(response.json.return_value)
    return response


def test_system_prompt_loaded_from_disk() -> None:
    """The system prompt lives in `prompts/triage/<label>.md`; the module
    reads it at import time. Guards against accidental inline prompts
    creeping back in + against the file being renamed/moved without
    updating the label constant. Mirrors the prompt-resolution test
    pattern used by `workflows.extraction`."""
    from orchestrators.defs.triage_knowledge_queue import content_shape_llm

    assert "content_shape" in content_shape_llm._SYSTEM_PROMPT
    # All six valid shape strings should appear in the prompt body so the
    # LLM can reason about them as the enum we'll then validate against.
    for shape in (
        "conference_talk",
        "podcast_episode",
        "tutorial",
        "opinion_essay",
        "research_summary",
        "unknown",
    ):
        assert shape in content_shape_llm._SYSTEM_PROMPT


def test_no_keys_configured_returns_unknown() -> None:
    """When neither Groq nor OpenAI key is set, the resource skips the
    HTTP call entirely and returns SHAPE_UNKNOWN with a metadata marker
    that explains why. Lets the asset stay green when the deploy hasn't
    been configured with an LLM key yet."""
    classifier = ContentShapeClassifier(groq_api_key=None, openai_api_key=None)

    shape, metadata = classifier.classify(
        enrichment=EnrichmentSignals(),
        content_type="article",
        url="https://example.com/post",
    )

    assert shape == SHAPE_UNKNOWN
    assert metadata == {"status": "skipped_no_key"}


def test_groq_returns_valid_shape() -> None:
    """Happy path: Groq tier responds with valid JSON naming a valid
    shape. Resource returns (shape, ok-metadata) without touching the
    OpenAI fallback. Metadata records the model that answered so we can
    eyeball post-deploy which tier is firing."""
    classifier = ContentShapeClassifier(groq_api_key="g-key", openai_api_key="o-key")

    with patch(
        "workflows.llm_cascade.httpx.post",
        return_value=_mock_chat_response("tutorial"),
    ) as mock_post:
        shape, metadata = classifier.classify(
            enrichment=EnrichmentSignals(
                article=ArticleSignals(title="Build X with Y", description="A walkthrough.")
            ),
            content_type="article",
            url="https://example.com/build-x",
        )

    assert shape == "tutorial"
    assert metadata["status"] == "ok"
    assert metadata["model"] == "llama-3.3-70b-versatile"

    # Only Groq was called — OpenAI fallback should not fire on success.
    assert mock_post.call_count == 1
    call_url = mock_post.call_args.args[0]
    assert "groq.com" in call_url


def test_llm_returning_unknown_is_honored() -> None:
    """The prompt explicitly lists `unknown` as a valid output for cases
    where signal is insufficient. When the LLM picks it, the resource
    surfaces it as a distinct status (so post-deploy we can tell honest
    abstention apart from exception fall-through) and does NOT cascade
    to the fallback tier — the model spoke; trust the answer."""
    classifier = ContentShapeClassifier(groq_api_key="g-key", openai_api_key="o-key")

    with patch(
        "workflows.llm_cascade.httpx.post",
        return_value=_mock_chat_response("unknown"),
    ) as mock_post:
        shape, metadata = classifier.classify(
            enrichment=EnrichmentSignals(),
            content_type="other",
            url="https://example.com/ambiguous",
        )

    assert shape == SHAPE_UNKNOWN
    assert metadata["status"] == "returned_unknown"
    assert metadata["model"] == "llama-3.3-70b-versatile"
    assert mock_post.call_count == 1


def test_groq_returning_invalid_shape_falls_through_to_openai() -> None:
    """When Groq emits a string that isn't in `ALL_CONTENT_SHAPES` (model
    drift / prompt drift / a brand-new value we haven't taught it), treat
    it as a tier failure and try the next tier. Different model might
    behave better; we shouldn't trust a value we can't route on."""
    classifier = ContentShapeClassifier(groq_api_key="g-key", openai_api_key="o-key")

    with patch(
        "workflows.llm_cascade.httpx.post",
        side_effect=[
            _mock_chat_response("newsletter"),  # Groq emits invalid shape
            _mock_chat_response("opinion_essay"),  # OpenAI fallback recovers
        ],
    ) as mock_post:
        shape, metadata = classifier.classify(
            enrichment=EnrichmentSignals(),
            content_type="article",
            url="https://example.com/post",
        )

    assert shape == "opinion_essay"
    assert metadata["status"] == "ok"
    assert metadata["model"] == "gpt-4.1-mini"
    assert mock_post.call_count == 2
    # Groq URL first, OpenAI URL second.
    assert "groq.com" in mock_post.call_args_list[0].args[0]
    assert "openai.com" in mock_post.call_args_list[1].args[0]


def test_groq_exception_falls_through_to_openai() -> None:
    """Network blip on Groq (timeout / 5xx / DNS) → try the next tier
    rather than failing the whole triage. Groq's free tier has documented
    flakiness; OpenAI as fallback is exactly why the cascade exists."""
    classifier = ContentShapeClassifier(groq_api_key="g-key", openai_api_key="o-key")

    with patch(
        "workflows.llm_cascade.httpx.post",
        side_effect=[
            httpx.ConnectTimeout("groq dropped the connection"),
            _mock_chat_response("tutorial"),
        ],
    ) as mock_post:
        shape, metadata = classifier.classify(
            enrichment=EnrichmentSignals(),
            content_type="article",
            url="https://example.com/post",
        )

    assert shape == "tutorial"
    assert metadata["status"] == "ok"
    assert metadata["model"] == "gpt-4.1-mini"
    assert mock_post.call_count == 2


def test_all_tiers_fail_returns_unknown_with_exception_status() -> None:
    """Both tiers throw → resource still returns cleanly. Triage proceeds
    with content_shape="unknown"; the user disambiguates in Notion."""
    classifier = ContentShapeClassifier(groq_api_key="g-key", openai_api_key="o-key")

    with patch(
        "workflows.llm_cascade.httpx.post",
        side_effect=[
            httpx.ConnectTimeout("groq down"),
            httpx.ReadTimeout("openai slow"),
        ],
    ) as mock_post:
        shape, metadata = classifier.classify(
            enrichment=EnrichmentSignals(),
            content_type="article",
            url="https://example.com/post",
        )

    assert shape == SHAPE_UNKNOWN
    # Status is "invalid_output" today since the cascade treats every
    # tier-skip the same way. Acceptable for v1; finer-grained status
    # (exception vs invalid_output) would need attempt logging.
    assert metadata["status"] == "invalid_output"
    assert mock_post.call_count == 2
