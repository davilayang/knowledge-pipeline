"""Source-agnostic transcript structurer.

Takes a wall-of-text transcript markdown blob + speaker-hint context
(title, author/show, optional date) and returns speaker-attributed,
paragraph-shaped markdown with punctuation restored and specifics preserved.

First caller: YouTube handler (auto-caption transcripts via youtube-transcript-api).
Second caller: podcast handler (Whisper transcripts, planned, TODO #3).
Third caller: POST /v1/structure-transcript endpoint (planned).

Keep this module free of source-specific assumptions (no video_id, no URL,
no oembed dependencies). Per-source plumbing belongs in handlers / endpoint.
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
    return await call_cloud_chain(
        raw_markdown,
        _PROMPT,
        chain=_CHAIN,
        openai_key=getattr(ctx, "openai_api_key", None),
        ollama_key=getattr(ctx, "ollama_api_key", None),
        title=title,
        content_date=content_date,
        author_name=author,
    )
