"""Langfuse v3 helpers for the OpenAI-native tracing path.

Tracing is automatic: `workflows.llm` uses the `langfuse.openai` drop-in, which
creates a generation observation for every call (nested under the active span).
This module only provides the env gate and the end-of-run flush — no callback
plumbing, no LangChain.
"""

import os


def langfuse_enabled() -> bool:
    """True when Langfuse tracing is configured (public key present).

    Gate construction/flush on this so the code stays silent — no "client
    disabled" warning — when the env vars are unset.
    """
    return bool(os.environ.get("LANGFUSE_PUBLIC_KEY"))


def flush_langfuse() -> None:
    """Flush buffered Langfuse events; no-op when unconfigured.

    Langfuse v3 buffers spans/generations and ships them in the background. A
    short-lived process (a Dagster asset run) must flush before it exits or the
    last items are dropped. Safe to call unconditionally.
    """
    if not langfuse_enabled():
        return
    from langfuse import get_client

    get_client().flush()
