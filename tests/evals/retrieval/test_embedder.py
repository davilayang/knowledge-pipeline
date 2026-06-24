"""Tests for OpenAIEmbedder retry policy.

Mocks ``OpenAI`` at its import location in ``retrievers.embedding.openai`` per
CLAUDE.md's "patch at the import location" rule.
"""

from unittest.mock import MagicMock, patch

import pytest
from openai import (
    APIConnectionError,
    AuthenticationError,
    InternalServerError,
    RateLimitError,
)
from retrievers.embedding.openai import OpenAIEmbedder


def _stub_response(vectors: list[list[float]], *, indices: list[int] | None = None) -> MagicMock:
    resp = MagicMock()
    idx = indices if indices is not None else list(range(len(vectors)))
    resp.data = [MagicMock(embedding=v, index=i) for v, i in zip(vectors, idx, strict=True)]
    return resp


def _build_embedder(create_side_effect, *, dims: int | None = 1536):
    """Construct an OpenAIEmbedder whose .embeddings.create is mocked."""
    with patch("retrievers.embedding.openai.OpenAI") as mock_openai:
        client = MagicMock()
        client.embeddings.create.side_effect = create_side_effect
        mock_openai.return_value = client
        embedder = OpenAIEmbedder("text-embedding-3-small", dims, api_key="fake")
    # Drop the wait between retries so tests don't sleep for tens of seconds;
    # retry/stop policy stays unchanged so we still exercise the real classes.
    from tenacity import Retrying, retry_if_exception_type, stop_after_attempt, wait_none

    embedder._retry_policy = Retrying(
        wait=wait_none(),
        stop=stop_after_attempt(6),
        retry=retry_if_exception_type((RateLimitError, APIConnectionError, InternalServerError)),
        reraise=True,
    )
    return embedder, client


class TestRetryBehavior:
    def test_succeeds_on_first_attempt(self):
        embedder, client = _build_embedder([_stub_response([[0.1, 0.2]])])
        out = embedder.embed_batch(["hello"])
        assert out == [[0.1, 0.2]]
        assert client.embeddings.create.call_count == 1

    def test_retries_rate_limit_then_succeeds(self):
        rl = RateLimitError("throttled", response=MagicMock(status_code=429), body=None)
        embedder, client = _build_embedder([rl, rl, _stub_response([[0.1]])])
        out = embedder.embed_batch(["hi"])
        assert out == [[0.1]]
        assert client.embeddings.create.call_count == 3

    def test_retries_connection_error(self):
        ce = APIConnectionError(request=MagicMock())
        embedder, client = _build_embedder([ce, _stub_response([[0.5]])])
        out = embedder.embed_batch(["hi"])
        assert out == [[0.5]]
        assert client.embeddings.create.call_count == 2

    def test_retries_internal_server_error(self):
        ise = InternalServerError("boom", response=MagicMock(status_code=500), body=None)
        embedder, client = _build_embedder([ise, _stub_response([[0.7]])])
        out = embedder.embed_batch(["hi"])
        assert out == [[0.7]]
        assert client.embeddings.create.call_count == 2

    def test_does_not_retry_authentication_error(self):
        # AuthenticationError is a 4xx — non-transient. Must raise immediately,
        # not burn the attempt budget.
        auth = AuthenticationError("bad key", response=MagicMock(status_code=401), body=None)
        embedder, client = _build_embedder([auth])
        with pytest.raises(AuthenticationError):
            embedder.embed_batch(["hi"])
        assert client.embeddings.create.call_count == 1

    def test_empty_input_skips_api(self):
        embedder, client = _build_embedder([])
        assert embedder.embed_batch([]) == []
        assert client.embeddings.create.call_count == 0


class TestOpenAICompatible:
    """The same class drives any OpenAI-compatible server (llama.cpp, Ollama,
    vLLM) via `base_url` + `dims=None` — llama.cpp's /v1/embeddings rejects the
    `dimensions` param and uses the model's native dim."""

    def test_base_url_is_passed_to_the_client(self):
        with patch("retrievers.embedding.openai.OpenAI") as mock_openai:
            OpenAIEmbedder(
                "nomic-embed-text-v1.5",
                dims=None,
                base_url="http://localhost:8080/v1",
                api_key="no-key",
            )
        assert mock_openai.call_args.kwargs.get("base_url") == "http://localhost:8080/v1"

    def test_dimensions_omitted_when_dims_is_none(self):
        embedder, client = _build_embedder([_stub_response([[0.1, 0.2]])], dims=None)
        embedder.embed_batch(["hi"])
        assert "dimensions" not in client.embeddings.create.call_args.kwargs

    def test_dimensions_sent_when_dims_set(self):
        embedder, client = _build_embedder([_stub_response([[0.1, 0.2]])], dims=1536)
        embedder.embed_batch(["hi"])
        assert client.embeddings.create.call_args.kwargs.get("dimensions") == 1536

    def test_realigns_embeddings_by_response_index(self):
        # API returns rows out of input order; embed_batch must realign by `index`
        # so out[i] is the embedding for texts[i] (not the response's row order).
        resp = _stub_response([[9.0], [1.0], [5.0]], indices=[2, 0, 1])
        embedder, _ = _build_embedder([resp])
        assert embedder.embed_batch(["a", "b", "c"]) == [[1.0], [5.0], [9.0]]
