from unittest.mock import MagicMock, patch

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from pydantic import BaseModel

from knowledge_pipeline.lib.llm import generate, generate_structured


@patch("knowledge_pipeline.lib.llm.get_llm")
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


@patch("knowledge_pipeline.lib.llm.get_llm")
def test_generate_with_system(mock_get_llm):
    mock_llm = MagicMock()
    mock_llm.invoke.return_value = AIMessage(content="Hello")
    mock_get_llm.return_value = mock_llm

    generate("What is RAG?", system="You are a helpful assistant.")

    messages = mock_llm.invoke.call_args[0][0]
    assert len(messages) == 2
    assert isinstance(messages[0], SystemMessage)
    assert isinstance(messages[1], HumanMessage)


@patch("knowledge_pipeline.lib.llm.get_llm")
def test_generate_custom_model(mock_get_llm):
    mock_llm = MagicMock()
    mock_llm.invoke.return_value = AIMessage(content="Hello")
    mock_get_llm.return_value = mock_llm

    generate("Hello", model="gpt-4.1-nano")

    mock_get_llm.assert_called_once_with("gpt-4.1-nano")


@patch("knowledge_pipeline.lib.llm.get_llm")
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


@patch("knowledge_pipeline.lib.llm.get_llm")
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
