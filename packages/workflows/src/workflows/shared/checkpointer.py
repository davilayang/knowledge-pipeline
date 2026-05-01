import os
from collections.abc import Iterator
from contextlib import contextmanager

from langgraph.checkpoint.postgres import PostgresSaver


@contextmanager
def get_checkpointer(db_url: str | None = None) -> Iterator[PostgresSaver]:
    """Yield a PostgresSaver bound to a fresh psycopg connection.

    Falls back to DATABASE_URL when db_url is not supplied. Calls setup()
    on entry — idempotent, ensures the langgraph_checkpoints tables exist.
    Connection closes on context exit.

    Usage:
        with get_checkpointer() as checkpointer:
            graph = builder.compile(checkpointer=checkpointer)
            graph.invoke(state, config={"configurable": {"thread_id": ...}})
    """
    url = db_url or os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "get_checkpointer() needs a Postgres URL — pass db_url= or set "
            "DATABASE_URL in the environment."
        )
    with PostgresSaver.from_conn_string(url) as saver:
        saver.setup()
        yield saver
