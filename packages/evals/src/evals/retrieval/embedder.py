"""Embedder protocol + OpenAI implementation."""

import hashlib
from typing import Protocol

from openai import (
    APIConnectionError,
    InternalServerError,
    OpenAI,
    RateLimitError,
)
from tenacity import (
    Retrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_random_exponential,
)

# Only retry transient failures. ``APIError`` is the SDK's base class for all
# API errors including 4xx (BadRequestError, AuthenticationError) — retrying
# those would burn the attempt budget on a misconfigured key.
_TRANSIENT_OPENAI_ERRORS = (RateLimitError, APIConnectionError, InternalServerError)


class Embedder(Protocol):
    model: str
    dims: int

    def embed_batch(self, texts: list[str]) -> list[list[float]]: ...


class OpenAIEmbedder:
    """Wraps the OpenAI embeddings API with tenacity retry on rate-limit,
    connection, and 5xx errors. ``dimensions`` exploits Matryoshka
    representation learning: ``text-embedding-3-{small,large}`` accept
    ``dimensions=N`` to return a properly truncated vector (N ≤ native_dims).
    """

    def __init__(self, model: str, dims: int, *, api_key: str | None = None):
        self.model = model
        self.dims = dims
        self._client = OpenAI(api_key=api_key) if api_key else OpenAI()
        self._retry_policy = Retrying(
            wait=wait_random_exponential(multiplier=1, max=30),
            stop=stop_after_attempt(6),
            retry=retry_if_exception_type(_TRANSIENT_OPENAI_ERRORS),
            reraise=True,
        )

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        for attempt in self._retry_policy:
            with attempt:
                resp = self._client.embeddings.create(
                    model=self.model,
                    input=texts,
                    dimensions=self.dims,
                )
        return [list(d.embedding) for d in resp.data]


class DeterministicFakeEmbedder:
    """Hash-based deterministic embedder for tests."""

    def __init__(self, model: str = "fake", dims: int = 16):
        self.model = model
        self.dims = dims

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for text in texts:
            h = hashlib.sha256(text.encode("utf-8")).digest()
            vec = [(h[i % len(h)] / 255.0) for i in range(self.dims)]
            # L2-normalize so cosine distance behaves predictably.
            norm = sum(x * x for x in vec) ** 0.5 or 1.0
            out.append([x / norm for x in vec])
        return out
