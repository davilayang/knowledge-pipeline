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


def _stub_response(vectors: list[list[float]]) -> MagicMock:
    resp = MagicMock()
    resp.data = [MagicMock(embedding=v) for v in vectors]
    return resp


def _build_embedder(create_side_effect):
    """Construct an OpenAIEmbedder whose .embeddings.create is mocked."""
    with patch("retrievers.embedding.openai.OpenAI") as mock_openai:
        client = MagicMock()
        client.embeddings.create.side_effect = create_side_effect
        mock_openai.return_value = client
        embedder = OpenAIEmbedder("text-embedding-3-small", 1536, api_key="fake")
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
