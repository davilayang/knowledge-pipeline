# Thin wrapper around LangChain chat models.

from dataclasses import dataclass

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel

from workflows.shared.observability import get_langfuse_callback

_DEFAULT_MODEL = "gpt-4.1-mini"


@dataclass(frozen=True, slots=True)
class LLMCall:
    """Single LLM invocation result with usage metadata."""

    content: str
    model: str
    input_tokens: int
    output_tokens: int


def get_llm(model: str = _DEFAULT_MODEL) -> BaseChatModel:
    """Create a LangChain chat model instance.

    Reads OPENAI_API_KEY from environment automatically.
    To switch providers later, swap ChatOpenAI for ChatAnthropic etc.
    """
    return ChatOpenAI(model=model)


def _invoke_config() -> dict:
    cb = get_langfuse_callback()
    return {"callbacks": [cb]} if cb else {}


def _build_messages(prompt: str, system: str) -> list:
    messages = []
    if system:
        messages.append(SystemMessage(content=system))
    messages.append(HumanMessage(content=prompt))
    return messages


def _to_llm_call(response: AIMessage, fallback_model: str) -> LLMCall:
    usage = response.usage_metadata or {"input_tokens": 0, "output_tokens": 0}
    return LLMCall(
        content=response.content if isinstance(response.content, str) else "",
        model=response.response_metadata.get("model_name", fallback_model),
        input_tokens=usage.get("input_tokens", 0),
        output_tokens=usage.get("output_tokens", 0),
    )


def generate(
    prompt: str,
    *,
    system: str = "",
    model: str = _DEFAULT_MODEL,
) -> str:
    """Generate a chat completion and return the assistant's text response."""
    llm = get_llm(model)
    response = llm.invoke(_build_messages(prompt, system), config=_invoke_config())
    return response.content


def generate_with_usage(
    prompt: str,
    *,
    system: str = "",
    model: str = _DEFAULT_MODEL,
) -> LLMCall:
    """Like generate(), but also returns token usage metadata."""
    llm = get_llm(model)
    response = llm.invoke(_build_messages(prompt, system), config=_invoke_config())
    return _to_llm_call(response, fallback_model=model)


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
    return structured_llm.invoke(_build_messages(prompt, system), config=_invoke_config())


def generate_structured_with_usage[
    T: BaseModel
](
    prompt: str,
    *,
    schema: type[T],
    system: str = "",
    model: str = _DEFAULT_MODEL,
) -> tuple[
    T, LLMCall
]:
    """Like generate_structured(), but also returns token usage.

    Uses include_raw=True so the raw AIMessage (with usage_metadata) and the
    parsed model come back from one call — no second LLM round-trip.
    """
    llm = get_llm(model)
    structured_llm = llm.with_structured_output(schema, include_raw=True)
    result = structured_llm.invoke(_build_messages(prompt, system), config=_invoke_config())
    # include_raw=True captures parse errors instead of raising. Re-raise to
    # match the bare generate_structured() contract (caller wants fail-fast,
    # not a None payload that crashes later).
    err = result.get("parsing_error")
    if err is not None:
        raise err
    parsed: T = result["parsed"]
    raw: AIMessage = result["raw"]
    return parsed, _to_llm_call(raw, fallback_model=model)
