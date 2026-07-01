"""Shared prompt-cache prefix for the extract-time LLM calls (claims + entities).

Both `extract_claims` and `extract_entities` read the SAME raw article at extract
time. To let the second call reuse OpenAI's server-side prefix cache, the two must
send a byte-identical leading prefix — the shared system message plus the article
envelope — with only the final task message differing. This module owns that
prefix construction so the two callers cannot drift out of cache alignment (a
divergence of even one byte before the task tail voids the cache).

The envelope carries ONLY stable content (title, author, body): no task text and
nothing volatile (e.g. a known-entity catalog that grows over time), so the cached
prefix stays valid across calls and across ticks.
"""

from domains.types import IngestItem

from workflows.wiki_synthesis.prompts import EXTRACT_ARTICLE_ENVELOPE, EXTRACT_SHARED_SYSTEM


def article_envelope(item: IngestItem) -> str:
    """The shared, cacheable article block — identical across the claims and
    entities calls for the same item."""
    author_line = f"Author: {item.author}\n" if item.author else ""
    return EXTRACT_ARTICLE_ENVELOPE.format(
        title=item.title, author_line=author_line, article_text=item.text
    )


def shared_prefix_messages(item: IngestItem, task: str) -> list[dict[str, str]]:
    """`[shared system, article envelope, task]` — the message list for one
    extract-time call. The first two messages are byte-identical across the claims
    and entities calls (so the article prompt-caches); `task` is the only
    differing, per-call tail."""
    return [
        {"role": "system", "content": EXTRACT_SHARED_SYSTEM},
        {"role": "user", "content": article_envelope(item)},
        {"role": "user", "content": task},
    ]
