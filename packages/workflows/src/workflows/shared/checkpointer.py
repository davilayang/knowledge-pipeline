from collections.abc import Iterator
from contextlib import contextmanager

from langgraph.checkpoint.postgres import PostgresSaver


@contextmanager
def get_checkpointer(db_url: str) -> Iterator[PostgresSaver]:
    """Yield a PostgresSaver bound to a fresh psycopg connection.

    Calls setup() on entry — idempotent, ensures the langgraph_checkpoints tables
    exist. Connection closes on context exit.

    Usage:
        with get_checkpointer(db_url) as checkpointer:
            graph = builder.compile(checkpointer=checkpointer)
            graph.invoke(state, config={"configurable": {"thread_id": ...}})
    """

    with PostgresSaver.from_conn_string(db_url) as saver:
        saver.setup()
        yield saver
