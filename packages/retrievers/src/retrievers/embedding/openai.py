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
    dims: int | None

    def embed_batch(self, texts: list[str]) -> list[list[float]]: ...


class OpenAIEmbedder:
    """Embeds via the OpenAI embeddings API — or ANY OpenAI-compatible server
    (llama.cpp's ``llama-server --embeddings``, Ollama, vLLM, LM Studio) by
    pointing ``base_url`` at it. Tenacity retry on rate-limit / connection / 5xx.

    ``dims`` controls OpenAI's Matryoshka truncation: ``text-embedding-3-{small,
    large}`` accept ``dimensions=N`` (N ≤ native_dims). Pass ``dims=None`` for
    backends that don't support that param — notably llama.cpp's
    ``/v1/embeddings``, which rejects ``dimensions`` and returns the model's
    native dim (already L2-normalized). A local server enforces no key — pass
    ``api_key="no-key"``.
    """

    def __init__(
        self,
        model: str,
        dims: int | None = None,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
    ):
        self.model = model
        self.dims = dims
        client_kwargs: dict[str, str] = {}
        if api_key:
            client_kwargs["api_key"] = api_key
        if base_url:
            client_kwargs["base_url"] = base_url
        self._client = OpenAI(**client_kwargs)
        self._retry_policy = Retrying(
            wait=wait_random_exponential(multiplier=1, max=30),
            stop=stop_after_attempt(6),
            retry=retry_if_exception_type(_TRANSIENT_OPENAI_ERRORS),
            reraise=True,
        )

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        kwargs: dict[str, object] = {"model": self.model}
        if self.dims is not None:
            kwargs["dimensions"] = self.dims
        out: list[list[float]] = []
        for sub in self._sub_batches(texts):
            for attempt in self._retry_policy:
                with attempt:
                    resp = self._client.embeddings.create(input=sub, **kwargs)
            # Realign by `index` to handle a reordered/partial response.
            by_index = {d.index: d.embedding for d in resp.data}
            if by_index.keys() != set(range(len(sub))):
                raise ValueError(
                    f"embeddings response index set {sorted(by_index)} != "
                    f"expected 0..{len(sub) - 1}"
                )
            out.extend(list(by_index[i]) for i in range(len(sub)))
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
