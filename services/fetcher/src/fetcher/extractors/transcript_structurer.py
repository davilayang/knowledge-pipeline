"""Source-agnostic transcript structurer.

Takes a wall-of-text transcript markdown blob + speaker-hint context
(title, author/show, optional date) and returns speaker-attributed,
paragraph-shaped markdown with punctuation restored and specifics preserved.

Callers: YouTube handler (auto-caption transcripts), podcast handler (Whisper
transcripts, planned), and the planned `POST /v1/structure-transcript` endpoint.

Keep this module free of source-specific assumptions (no video_id, no URL, no
oembed dependencies) -- per-source plumbing belongs in handlers / endpoint.
"""

import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fetcher.extractors._cloud_chain import (
    ChainEntry,
    StructurerChainFailed,
    _load_chain,
    call_cloud_chain,
)


if TYPE_CHECKING:
    from fetcher.types import FetchContext


logger = logging.getLogger(__name__)


__all__ = [
    "StructurerChainFailed",
    "call_cloud_chain",
    "get_chain",
    "get_prompt",
    "structure_transcript",
]


_CHAIN: list[ChainEntry] = _load_chain(
    Path(
        os.environ.get(
            "FETCHER_TRANSCRIPT_STRUCTURER_CONFIG_PATH",
            "config/transcript_structurer.yaml",
        )
    )
)


# Recall catches wholesale loss; the gap ceiling catches rewriting that recall
# alone can't see (see `fidelity.long_gaps_per_10k` for why raising the gap
# threshold doesn't mean "stricter"). Thresholds set from the transcript
# corpus: faithful outputs sit under 3.3 gaps per 10k characters, the two known
# rewrites sit at 8.7 and 22.6, and a 59.5%-recall talk was still the most
# faithful output produced for it -- so a recall floor alone can't catch this.
_MIN_RETENTION = 0.5
_MAX_GAPS_PER_10K = 5.0


# Fidelity falls off continuously with input length rather than at a cliff, so
# the limit is set by what recovers, not by where collapse starts. The two
# hardest transcripts in the corpus score 70-80% trigram recall unsplit, 86-91%
# at this limit, and 93% at 8,000 -- not worth doubling the call count for.
_MAX_CHUNK_CHARS = 12_000


# Without this, each segment comes back with its own title, opening, and
# wrap-up -- concatenating them yields a document with several introductions.
_FRAGMENT_NOTE = (
    "\n\nThis input is one fragment of a longer transcript. Do not add a title, "
    "an introduction, or a conclusion. Begin and end mid-conversation."
)


_PROMPT_PATH: Path = Path(
    os.environ.get(
        "FETCHER_TRANSCRIPT_STRUCTURER_PROMPT_PATH",
        "prompts/structure_transcript_v1.md",
    )
)


def _load_prompt(path: Path) -> str:
    try:
        return path.read_text()
    except FileNotFoundError:
        logger.warning(
            "transcript structurer prompt not found at %s; cloud stage will use empty prompt",
            path,
        )
        return ""


_PROMPT: str = _load_prompt(_PROMPT_PATH)


def get_chain() -> list[ChainEntry]:
    return list(_CHAIN)


def get_prompt() -> str:
    return _PROMPT


def _split(text: str, limit: int) -> list[str]:
    """Split into segments of at most `limit` chars, cutting at whitespace.

    Auto-caption transcripts arrive as one unbroken run of words -- no sentence
    punctuation, and no newlines unless the source had pauses long enough for
    `chunks_to_markdown` to insert them. So the last space before the limit is
    the best available seam. A segment starting mid-sentence is fine -- the
    whole transcript already looks that way to the model.
    """
    if len(text) <= limit:
        return [text]
    segments: list[str] = []
    start = 0
    while start < len(text):
        if len(text) - start <= limit:
            segments.append(text[start:])
            break
        cut = text.rfind(" ", start, start + limit)
        if cut <= start:
            cut = start + limit
        segments.append(text[start:cut])
        start = cut + 1
    return segments


def _merge_usage(usages: list[dict[str, Any]]) -> dict[str, Any]:
    """Fold per-segment usage into one payload with the same shape as a single call."""
    head = next((u for u in usages if u), {})
    totals: dict[str, Any] = dict(head)
    for field in ("tokens_in", "tokens_out", "cached_tokens", "duration_ms"):
        values = [u.get(field) for u in usages if u and u.get(field) is not None]
        totals[field] = sum(values) if values else None
    reasons = {u.get("finish_reason") for u in usages if u}
    totals["finish_reason"] = (
        "stop" if reasons == {"stop"} else next(iter(reasons - {"stop"}), None)
    )
    totals["segments"] = len(usages)
    return totals


async def structure_transcript(
    ctx: "FetchContext",
    raw_markdown: str,
    *,
    title: str | None,
    author: str | None,
    content_date: str | None = None,
) -> tuple[str, str, dict[str, Any]]:
    """Run the cloud chain over raw transcript markdown.

    Returns (structured_markdown, "structurer:<model>", usage_payload).
    Raises StructurerChainFailed when every chain entry fails or no keys are
    configured — caller decides whether to fall back to raw_markdown.
    """
    segments = _split(raw_markdown, _MAX_CHUNK_CHARS)
    prompt = _PROMPT if len(segments) == 1 else _PROMPT + _FRAGMENT_NOTE

    structured: list[str] = []
    usages: list[dict[str, Any]] = []
    tier = ""
    for segment in segments:
        # Sequential, not gathered: Ollama Cloud's concurrency limits are
        # unknown, and a partial failure mid-gather is harder to reason about.
        markdown, tier, usage = await call_cloud_chain(
            segment,
            prompt,
            chain=_CHAIN,
            openai_key=getattr(ctx, "openai_api_key", None),
            ollama_key=getattr(ctx, "ollama_api_key", None),
            title=title,
            content_date=content_date,
            author_name=author,
            min_retention=_MIN_RETENTION,
            max_gaps_per_10k=_MAX_GAPS_PER_10K,
        )
        structured.append(markdown)
        usages.append(usage)
    return "\n\n".join(structured), tier, _merge_usage(usages)
