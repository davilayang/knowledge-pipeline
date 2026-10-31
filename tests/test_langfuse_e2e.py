"""Round trip of the `workflows.llm` tracing surface through Langfuse Cloud.

Makes one real OpenAI call, on the module's default model, through
`workflows.llm.generate`, flushes, then reads the generation back over
Langfuse's REST API. The one place in this suite
where an LLM call is not mocked: what it checks is that the SDK's export
reaches Langfuse, which no mock can show. Skipped unless `LANGFUSE_E2E=1` and
the Langfuse and OpenAI keys are set, so `poe test` and CI never collect a call.

Run after any Langfuse SDK change:
    LANGFUSE_E2E=1 uv run --env-file .env pytest tests/test_langfuse_e2e.py -v

Readback uses plain httpx, not the SDK under test, so an SDK bump cannot break
the writer and the reader together. Data lands under environment `e2e`.
"""

import os
import time
import uuid

import httpx
import pytest

pytestmark = pytest.mark.skipif(
    not (
        os.environ.get("LANGFUSE_E2E") == "1"
        and os.environ.get("LANGFUSE_PUBLIC_KEY")
        and os.environ.get("LANGFUSE_SECRET_KEY")
        and os.environ.get("OPENAI_API_KEY")
    ),
    reason="Langfuse Cloud round trip; set LANGFUSE_E2E=1 with Langfuse + OpenAI keys",
)

# Long enough for normal ingestion, short enough to fail on the up-to-15-minute
# delay Cloud v4 applies to exports missing the v4 ingestion header.
_READBACK_TIMEOUT_S = 30


def _observations(session_id: str) -> list[dict]:
    base = os.environ.get("LANGFUSE_BASE_URL", "https://cloud.langfuse.com").rstrip("/")
    auth = (os.environ["LANGFUSE_PUBLIC_KEY"], os.environ["LANGFUSE_SECRET_KEY"])
    deadline = time.monotonic() + _READBACK_TIMEOUT_S
    while True:
        resp = httpx.get(
            f"{base}/api/public/v2/observations",
            params={"sessionId": session_id, "fields": "core,basic,io,model,usage"},
            auth=auth,
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()["data"]
        if data or time.monotonic() > deadline:
            return data
        time.sleep(2)


def test_generation_reaches_langfuse(monkeypatch):
    monkeypatch.setenv("LANGFUSE_TRACING_ENVIRONMENT", "e2e")
    from langfuse import get_client, propagate_attributes
    from workflows.llm import generate

    session_id = f"e2e-{uuid.uuid4()}"
    with propagate_attributes(session_id=session_id):
        generate("Reply with the single word: ok")
    get_client().flush()

    observations = _observations(session_id)
    generations = [o for o in observations if o["type"] == "GENERATION"]
    assert len(generations) == 1, observations
    gen = generations[0]
    assert gen["environment"] == "e2e"
    assert gen.get("providedModelName") or gen.get("model")
    assert gen["input"] and gen["output"]
    assert gen.get("usageDetails", {}).get("input", 0) > 0
