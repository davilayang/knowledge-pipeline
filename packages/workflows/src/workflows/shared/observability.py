import os
from functools import lru_cache

from langfuse.langchain import CallbackHandler


@lru_cache(maxsize=1)
def get_langfuse_callback() -> CallbackHandler | None:
    """Return a process-cached Langfuse callback handler, or None if unconfigured.

    Skipping instantiation when LANGFUSE_PUBLIC_KEY is unset avoids the
    "client will be disabled" warning that Langfuse logs at construction
    time. Clear the cache (`get_langfuse_callback.cache_clear()`) if env
    changes mid-process — useful in tests.
    """
    if not os.environ.get("LANGFUSE_PUBLIC_KEY"):
        return None
    return CallbackHandler()
