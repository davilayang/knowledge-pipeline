"""Pin `narrative_v3` above the size at which OpenAI actually caches its prefix.

Of the four extraction calls, only `narrative` leads with its own prompt file:
its messages are `[system: prompt, user: article]`, so the prompt body is the
only text that repeats across items and therefore the only thing that can be a
cached prefix. `metadata`, `topic_card` and `followups` all lead with the short
`SHARED_SYSTEM` and reuse the article behind it, so their leading static text is
deliberately below any floor -- this rule is narrative-specific.

The threshold is NOT OpenAI's documented 1024-token minimum. 1024 is the floor
when the whole request repeats; when only the leading system message repeats and
the user message differs -- the narrative call's shape -- caching starts far
higher. Measured 2026-08-30 against the production model gpt-5-mini, sending a
fresh article behind an identical system message each time:

    system prompt tokens | cached_tokens on the 2nd..4th call
                     897 | 0, 0, 0      (narrative_v2, the active prompt)
                    1225 | 0, 0, 0
                    1503 | 0, 0, 0
                    1705 | 0, 0, 0
                    1904 | 1792, 1792, 1792
                    2044 | 1792, 1792, 1792   (narrative_v3)

So the threshold sits between 1705 and 1904 tokens, and `narrative_v3` clears it
with roughly 140 tokens to spare. That margin is the reason this test exists:
the queued reshape of `narrative_v3` proposes cutting its `Salient threads:`
section, which would leave about 1448 tokens -- comfortably over the documented
1024 floor and comfortably under the size that actually caches. This test fails
on that cut rather than letting it ship a prompt that silently never caches.

Counting the prompt body alone under-counts the rendered prefix, which also
carries message framing, so a body over the threshold guarantees a rendered
prefix over it.
"""

import tiktoken
from domains.extraction.prompts import strip_design_notes
from orchestrators.defs.fetch_extract_queue.resources import _PROMPTS_DIR

# Smallest leading system prefix measured to produce a cache hit on gpt-5-mini
# when the user message behind it differs. 1705 tokens produced none.
NARRATIVE_CACHE_THRESHOLD_TOKENS = 1904


def test_narrative_v3_stays_large_enough_to_cache():
    body = strip_design_notes((_PROMPTS_DIR / "narrative_v3.md").read_text())
    tokens = len(tiktoken.get_encoding("o200k_base").encode(body))

    assert tokens >= NARRATIVE_CACHE_THRESHOLD_TOKENS, (
        f"narrative_v3 is {tokens} tokens, below the {NARRATIVE_CACHE_THRESHOLD_TOKENS} "
        "measured as the smallest narrative prompt that OpenAI caches across items. "
        "Shrinking it below that wins back output budget but gives up the prompt "
        "cache entirely -- the narrative call would report zero cached tokens on "
        "every item, as narrative_v2 does today."
    )
