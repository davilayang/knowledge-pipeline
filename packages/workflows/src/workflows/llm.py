# Thin wrapper around LangChain chat models.

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel

_DEFAULT_MODEL = "gpt-4.1-mini"


def get_llm(model: str = _DEFAULT_MODEL) -> BaseChatModel:
    """Create a LangChain chat model instance.

    Reads OPENAI_API_KEY from environment automatically.
    To switch providers later, swap ChatOpenAI for ChatAnthropic etc.
    """
    return ChatOpenAI(model=model)


def generate(
    prompt: str,
    *,
    system: str = "",
    model: str = _DEFAULT_MODEL,
) -> str:
    """Generate a chat completion and return the assistant's text response."""
    llm = get_llm(model)
    messages = []
    if system:
        messages.append(SystemMessage(content=system))
    messages.append(HumanMessage(content=prompt))

    response = llm.invoke(messages)
    return response.content


def generate_structured[
    T: BaseModel
](prompt: str, *, schema: type[T], system: str = "", model: str = _DEFAULT_MODEL,) -> T:
    """Generate a structured response validated against a Pydantic model.

    Uses LangChain's with_structured_output() for provider-native structured
    output when available, with tool-calling fallback.

    Args:
        prompt: The user message.
        schema: A Pydantic BaseModel class defining the expected output shape.
        system: Optional system message.
        model: Model identifier (default: gpt-4.1-mini).

    Returns:
        An instance of the schema class, validated by Pydantic.
    """
    llm = get_llm(model)
    structured_llm = llm.with_structured_output(schema)

    messages = []
    if system:
        messages.append(SystemMessage(content=system))
    messages.append(HumanMessage(content=prompt))

    return structured_llm.invoke(messages)
