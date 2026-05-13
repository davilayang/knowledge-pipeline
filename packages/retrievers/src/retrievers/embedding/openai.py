"""Embedder protocol + OpenAI implementation."""

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

# OpenAI's `/embeddings` accepts at most 300k input tokens per request. We
# under-shoot to leave headroom for tokenizer drift between our cheap char-based
# estimator and the real BPE count.
_MAX_TOKENS_PER_REQUEST = 250_000


def _estimate_tokens(text: str) -> int:
    # Approximate 4 chars/token — fine as a budget guard, not a billing oracle.
    return max(1, len(text) // 4)


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
        out: list[list[float]] = []
        for sub in self._sub_batches(texts):
            for attempt in self._retry_policy:
                with attempt:
                    resp = self._client.embeddings.create(
                        model=self.model,
                        input=sub,
                        dimensions=self.dims,
                    )
            out.extend(list(d.embedding) for d in resp.data)
        return out

    @staticmethod
    def _sub_batches(texts: list[str]) -> list[list[str]]:
        batches: list[list[str]] = []
        cur: list[str] = []
        cur_tokens = 0
        for t in texts:
            est = _estimate_tokens(t)
            if cur and cur_tokens + est > _MAX_TOKENS_PER_REQUEST:
                batches.append(cur)
                cur, cur_tokens = [], 0
            cur.append(t)
            cur_tokens += est
        if cur:
            batches.append(cur)
        return batches
