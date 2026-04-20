from unittest.mock import MagicMock, patch

import pytest

from knowledge_pipeline.lib.llm import generate, generate_json


@pytest.fixture
def mock_openai():
    """Mock OpenAI client with a canned response."""
    mock_choice = MagicMock()
    mock_choice.message.content = "Hello from the mock LLM"

    mock_response = MagicMock()
    mock_response.choices = [mock_choice]

    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = mock_response

    with patch("knowledge_pipeline.lib.llm._get_client", return_value=mock_client):
        yield mock_client


def test_generate_basic(mock_openai):
    result = generate("What is RAG?")

    assert result == "Hello from the mock LLM"
    mock_openai.chat.completions.create.assert_called_once()
    call_kwargs = mock_openai.chat.completions.create.call_args
    messages = call_kwargs.kwargs["messages"]
    assert len(messages) == 1
    assert messages[0]["role"] == "user"
    assert messages[0]["content"] == "What is RAG?"


def test_generate_with_system(mock_openai):
    result = generate("What is RAG?", system="You are a helpful assistant.")

    assert result == "Hello from the mock LLM"
    call_kwargs = mock_openai.chat.completions.create.call_args
    messages = call_kwargs.kwargs["messages"]
    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"


def test_generate_custom_model(mock_openai):
    generate("Hello", model="gpt-4.1-nano")

    call_kwargs = mock_openai.chat.completions.create.call_args
    assert call_kwargs.kwargs["model"] == "gpt-4.1-nano"


def test_generate_json_sets_response_format(mock_openai):
    mock_openai.chat.completions.create.return_value.choices[0].message.content = '{"key": "value"}'

    result = generate_json("Return JSON", system="Respond in JSON.")

    assert result == '{"key": "value"}'
    call_kwargs = mock_openai.chat.completions.create.call_args
    assert call_kwargs.kwargs["response_format"] == {"type": "json_object"}


def test_generate_missing_api_key():
    with patch.dict("os.environ", {}, clear=True):
        with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
            generate("test")
