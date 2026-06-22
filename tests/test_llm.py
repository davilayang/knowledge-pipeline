"""Tests for the OpenAI-native LLM wrapper.

These exercise the public contract (text / usage / structured parse) against a
mocked OpenAI client, not the SDK internals — the client is swapped at the
`workflows.llm._get_client` seam.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from pydantic import BaseModel
from workflows.llm import (
    LLMCall,
    generate,
    generate_structured,
    generate_structured_with_usage,
    generate_with_usage,
)


def _chat_response(content: str, *, model: str = "gpt-4.1-mini", usage=None):
    """Shape of openai client.chat.completions.create(...) return value."""
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        model=model,
        usage=usage,
    )


def _parse_response(parsed, *, content="", refusal=None, model="gpt-4.1-nano", usage=None):
    """Shape of openai client.beta.chat.completions.parse(...) return value."""
    message = SimpleNamespace(parsed=parsed, content=content, refusal=refusal)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)], model=model, usage=usage)


def _mock_client():
    client = MagicMock()
    return client


@patch("workflows.llm._get_client")
def test_generate_returns_text(mock_get_client):
    client = _mock_client()
    client.chat.completions.create.return_value = _chat_response("Hello from the mock LLM")
    mock_get_client.return_value = client

    result = generate("What is RAG?")

    assert result == "Hello from the mock LLM"
    messages = client.chat.completions.create.call_args.kwargs["messages"]
    assert messages == [{"role": "user", "content": "What is RAG?"}]


@patch("workflows.llm._get_client")
def test_generate_with_system_prepends_system_message(mock_get_client):
    client = _mock_client()
    client.chat.completions.create.return_value = _chat_response("Hello")
    mock_get_client.return_value = client

    generate("What is RAG?", system="You are a helpful assistant.")

    messages = client.chat.completions.create.call_args.kwargs["messages"]
    assert messages[0] == {"role": "system", "content": "You are a helpful assistant."}
    assert messages[1] == {"role": "user", "content": "What is RAG?"}


@patch("workflows.llm._get_client")
def test_generate_passes_custom_model(mock_get_client):
    client = _mock_client()
    client.chat.completions.create.return_value = _chat_response("Hello")
    mock_get_client.return_value = client

    generate("Hello", model="gpt-4.1-nano")

    assert client.chat.completions.create.call_args.kwargs["model"] == "gpt-4.1-nano"


@patch("workflows.llm._get_client")
def test_generate_with_usage_returns_token_counts(mock_get_client):
    client = _mock_client()
    client.chat.completions.create.return_value = _chat_response(
        "hi",
        model="gpt-4.1-mini",
        usage=SimpleNamespace(prompt_tokens=7, completion_tokens=3),
    )
    mock_get_client.return_value = client

    call = generate_with_usage("hello")

    assert isinstance(call, LLMCall)
    assert call.content == "hi"
    assert call.model == "gpt-4.1-mini"
    assert call.input_tokens == 7
    assert call.output_tokens == 3


@patch("workflows.llm._get_client")
def test_generate_with_usage_handles_missing_usage(mock_get_client):
    client = _mock_client()
    client.chat.completions.create.return_value = _chat_response("x", usage=None)
    mock_get_client.return_value = client

    call = generate_with_usage("hello", model="gpt-4.1-mini")

    assert call.input_tokens == 0
    assert call.output_tokens == 0


@patch("workflows.llm._get_client")
def test_generate_structured_returns_pydantic_model(mock_get_client):
    class Entity(BaseModel):
        name: str
        category: str

    expected = Entity(name="RAG", category="concept")
    client = _mock_client()
    client.beta.chat.completions.parse.return_value = _parse_response(expected)
    mock_get_client.return_value = client

    result = generate_structured("Extract entity", schema=Entity)

    assert result == expected
    assert client.beta.chat.completions.parse.call_args.kwargs["response_format"] is Entity


@patch("workflows.llm._get_client")
def test_generate_structured_with_system_prepends_system_message(mock_get_client):
    class Info(BaseModel):
        value: str

    client = _mock_client()
    client.beta.chat.completions.parse.return_value = _parse_response(Info(value="test"))
    mock_get_client.return_value = client

    generate_structured("Extract", schema=Info, system="Be precise.")

    messages = client.beta.chat.completions.parse.call_args.kwargs["messages"]
    assert messages[0] == {"role": "system", "content": "Be precise."}
    assert messages[1] == {"role": "user", "content": "Extract"}


@patch("workflows.llm._get_client")
def test_generate_structured_with_usage_returns_parsed_and_call(mock_get_client):
    class Info(BaseModel):
        value: str

    parsed = Info(value="ok")
    client = _mock_client()
    client.beta.chat.completions.parse.return_value = _parse_response(
        parsed,
        model="gpt-4.1-nano",
        usage=SimpleNamespace(prompt_tokens=5, completion_tokens=2),
    )
    mock_get_client.return_value = client

    result, call = generate_structured_with_usage("Extract", schema=Info)

    assert result is parsed
    assert call.model == "gpt-4.1-nano"
    assert call.input_tokens == 5
    assert call.output_tokens == 2


@patch("workflows.llm._get_client")
def test_generate_structured_raises_on_refusal(mock_get_client):
    class Info(BaseModel):
        value: str

    client = _mock_client()
    client.beta.chat.completions.parse.return_value = _parse_response(
        None, refusal="I can't help with that"
    )
    mock_get_client.return_value = client

    with pytest.raises(ValueError, match="refused structured output"):
        generate_structured_with_usage("Extract", schema=Info)


@patch("workflows.llm._get_client")
def test_generate_structured_raises_on_empty_parse(mock_get_client):
    class Info(BaseModel):
        value: str

    client = _mock_client()
    client.beta.chat.completions.parse.return_value = _parse_response(None)
    mock_get_client.return_value = client

    with pytest.raises(ValueError, match="no result"):
        generate_structured_with_usage("Extract", schema=Info)
