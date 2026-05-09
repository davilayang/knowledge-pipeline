from unittest.mock import MagicMock, patch

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from pydantic import BaseModel
from workflows.llm import (
    LLMCall,
    generate,
    generate_structured,
    generate_structured_with_usage,
    generate_with_usage,
)


@patch("workflows.llm.get_llm")
def test_generate_basic(mock_get_llm):
    mock_llm = MagicMock()
    mock_llm.invoke.return_value = AIMessage(content="Hello from the mock LLM")
    mock_get_llm.return_value = mock_llm

    result = generate("What is RAG?")

    assert result == "Hello from the mock LLM"
    mock_llm.invoke.assert_called_once()
    messages = mock_llm.invoke.call_args[0][0]
    assert len(messages) == 1
    assert isinstance(messages[0], HumanMessage)
    assert messages[0].content == "What is RAG?"


@patch("workflows.llm.get_llm")
def test_generate_with_system(mock_get_llm):
    mock_llm = MagicMock()
    mock_llm.invoke.return_value = AIMessage(content="Hello")
    mock_get_llm.return_value = mock_llm

    generate("What is RAG?", system="You are a helpful assistant.")

    messages = mock_llm.invoke.call_args[0][0]
    assert len(messages) == 2
    assert isinstance(messages[0], SystemMessage)
    assert isinstance(messages[1], HumanMessage)


@patch("workflows.llm.get_llm")
def test_generate_custom_model(mock_get_llm):
    mock_llm = MagicMock()
    mock_llm.invoke.return_value = AIMessage(content="Hello")
    mock_get_llm.return_value = mock_llm

    generate("Hello", model="gpt-4.1-nano")

    mock_get_llm.assert_called_once_with("gpt-4.1-nano")


@patch("workflows.llm.get_llm")
def test_generate_structured_returns_pydantic_model(mock_get_llm):
    class Entity(BaseModel):
        name: str
        category: str

    expected = Entity(name="RAG", category="concept")

    mock_llm = MagicMock()
    mock_structured_llm = MagicMock()
    mock_structured_llm.invoke.return_value = expected
    mock_llm.with_structured_output.return_value = mock_structured_llm
    mock_get_llm.return_value = mock_llm

    result = generate_structured("Extract entity", schema=Entity)

    assert isinstance(result, Entity)
    assert result.name == "RAG"
    assert result.category == "concept"
    mock_llm.with_structured_output.assert_called_once_with(Entity)


@patch("workflows.llm.get_llm")
def test_generate_structured_with_system(mock_get_llm):
    class Info(BaseModel):
        value: str

    mock_llm = MagicMock()
    mock_structured_llm = MagicMock()
    mock_structured_llm.invoke.return_value = Info(value="test")
    mock_llm.with_structured_output.return_value = mock_structured_llm
    mock_get_llm.return_value = mock_llm

    generate_structured("Extract", schema=Info, system="Be precise.")

    messages = mock_structured_llm.invoke.call_args[0][0]
    assert len(messages) == 2
    assert isinstance(messages[0], SystemMessage)
    assert isinstance(messages[1], HumanMessage)


@patch("workflows.llm.get_llm")
def test_generate_with_usage_returns_token_counts(mock_get_llm):
    response = AIMessage(
        content="hi",
        response_metadata={"model_name": "gpt-4.1-mini"},
        usage_metadata={"input_tokens": 7, "output_tokens": 3, "total_tokens": 10},
    )
    mock_llm = MagicMock()
    mock_llm.invoke.return_value = response
    mock_get_llm.return_value = mock_llm

    call = generate_with_usage("hello")

    assert isinstance(call, LLMCall)
    assert call.content == "hi"
    assert call.model == "gpt-4.1-mini"
    assert call.input_tokens == 7
    assert call.output_tokens == 3


@patch("workflows.llm.get_llm")
def test_generate_with_usage_handles_missing_usage_metadata(mock_get_llm):
    response = AIMessage(content="x", response_metadata={"model_name": "gpt-4.1-mini"})
    mock_llm = MagicMock()
    mock_llm.invoke.return_value = response
    mock_get_llm.return_value = mock_llm

    call = generate_with_usage("hello", model="gpt-4.1-mini")

    assert call.input_tokens == 0
    assert call.output_tokens == 0


@patch("workflows.llm.get_llm")
def test_generate_structured_with_usage_returns_parsed_and_call(mock_get_llm):
    class Info(BaseModel):
        value: str

    raw = AIMessage(
        content="",
        response_metadata={"model_name": "gpt-4.1-nano"},
        usage_metadata={"input_tokens": 5, "output_tokens": 2, "total_tokens": 7},
    )
    parsed = Info(value="ok")

    mock_llm = MagicMock()
    mock_structured_llm = MagicMock()
    mock_structured_llm.invoke.return_value = {
        "parsed": parsed,
        "raw": raw,
        "parsing_error": None,
    }
    mock_llm.with_structured_output.return_value = mock_structured_llm
    mock_get_llm.return_value = mock_llm

    result, call = generate_structured_with_usage("Extract", schema=Info)

    assert result is parsed
    assert call.model == "gpt-4.1-nano"
    assert call.input_tokens == 5
    assert call.output_tokens == 2
    mock_llm.with_structured_output.assert_called_once_with(Info, include_raw=True)


@patch("workflows.llm.get_llm")
def test_generate_structured_with_usage_reraises_parsing_error(mock_get_llm):
    """include_raw=True captures parse errors in result['parsing_error'];
    the helper must re-raise so callers don't get a silent None payload."""

    class Info(BaseModel):
        value: str

    boom = ValueError("schema validation failed")
    mock_llm = MagicMock()
    mock_structured_llm = MagicMock()
    mock_structured_llm.invoke.return_value = {
        "parsed": None,
        "raw": AIMessage(content="<garbage>"),
        "parsing_error": boom,
    }
    mock_llm.with_structured_output.return_value = mock_structured_llm
    mock_get_llm.return_value = mock_llm

    import pytest

    with pytest.raises(ValueError, match="schema validation failed"):
        generate_structured_with_usage("Extract", schema=Info)
