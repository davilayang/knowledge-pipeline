import os
from functools import lru_cache
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from langfuse.langchain import CallbackHandler


@lru_cache(maxsize=1)
def get_langfuse_callback() -> "CallbackHandler | None":
    """Return a process-cached Langfuse callback handler, or None if unconfigured.

    Skipping instantiation when LANGFUSE_PUBLIC_KEY is unset avoids the
    "client will be disabled" warning that Langfuse logs at construction
    time. Clear the cache (`get_langfuse_callback.cache_clear()`) if env
    changes mid-process — useful in tests.

    The langfuse.langchain import is deferred so this module loads even if
    `langchain` (a runtime dep of `langfuse.langchain.CallbackHandler`) is
    not installed — relevant for production images that strip the agents
    code path.
    """
    if not os.environ.get("LANGFUSE_PUBLIC_KEY"):
        return None
    from langfuse.langchain import CallbackHandler

    return CallbackHandler()
