"""The query embedder search uses: indexing's model and dims, one try, a caller-set timeout."""

import asyncio
import json

import httpx
import pytest
from openai import APITimeoutError
from retrievers.embedding import INDEX_DIMS, INDEX_MODEL, AsyncQueryEmbedder


def _embedder(handler) -> AsyncQueryEmbedder:  # type: ignore[no-untyped-def]
    return AsyncQueryEmbedder(
        api_key="test", http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )


def test_it_embeds_with_indexing_model_and_dims() -> None:
    sent: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "data": [{"index": 0, "embedding": [0.1, 0.2]}],
                "model": "m",
                "object": "list",
                "usage": {"prompt_tokens": 1, "total_tokens": 1},
            },
        )

    vec = asyncio.run(_embedder(handler).embed("rag", timeout=2.0))
    assert vec == [0.1, 0.2]
    assert sent[0]["model"] == INDEX_MODEL and sent[0]["dimensions"] == INDEX_DIMS
    assert sent[0]["input"] == ["rag"]


def test_it_never_retries() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(APITimeoutError):
        asyncio.run(_embedder(handler).embed("rag", timeout=0.5))
    assert calls == 1
