# Thin wrapper around OpenAI chat completions.

import os

from openai import OpenAI

_DEFAULT_MODEL = "gpt-4.1-mini"


def _get_client() -> OpenAI:
    """Create an OpenAI client from environment."""
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY environment variable is not set")
    return OpenAI(api_key=api_key)


def generate(
    prompt: str,
    *,
    system: str = "",
    model: str = _DEFAULT_MODEL,
) -> str:
    """Generate a chat completion and return the assistant's text response.

    Args:
        prompt: The user message.
        system: Optional system message.
        model: Model identifier (default: gpt-4.1-mini).

    Returns:
        The assistant's response text.
    """
    client = _get_client()

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    response = client.chat.completions.create(model=model, messages=messages)
    return response.choices[0].message.content


def generate_json(
    prompt: str,
    *,
    system: str = "",
    model: str = _DEFAULT_MODEL,
) -> str:
    """Generate a chat completion with JSON response format.

    Returns the raw JSON string — caller is responsible for parsing.

    Args:
        prompt: The user message (should instruct the model to produce JSON).
        system: Optional system message.
        model: Model identifier (default: gpt-4.1-mini).

    Returns:
        The assistant's JSON response as a string.
    """
    client = _get_client()

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    response = client.chat.completions.create(
        model=model,
        messages=messages,
        response_format={"type": "json_object"},
    )
    return response.choices[0].message.content
