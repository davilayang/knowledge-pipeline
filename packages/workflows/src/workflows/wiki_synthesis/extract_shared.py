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
from domains.wiki.units import build_citable_units, number_units

from workflows.wiki_synthesis.prompts import EXTRACT_ARTICLE_ENVELOPE, EXTRACT_SHARED_SYSTEM

# Sent by both extract-time calls so OpenAI routes them toward the same cache.
# The shared prefix only pays off if the pair lands on the same machine; the key
# is what asks for that. Lives here, beside the prefix builder, so the two cannot
# be changed independently.
EXTRACT_CACHE_KEY = "kp-wiki-extract"


def article_envelope(item: IngestItem) -> str:
    """The shared, cacheable article block — identical across the claims and
    entities calls for the same item.

    The body is split into citable units and numbered, so an extracted claim can
    cite the unit indices it came from and a verifier can check the claim text
    against those spans. Numbering is deterministic in the body, so the block
    stays cacheable."""
    author_line = f"Author: {item.author}\n" if item.author else ""
    return EXTRACT_ARTICLE_ENVELOPE.format(
        title=item.title,
        author_line=author_line,
        article_text=number_units(build_citable_units(item.text)),
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
