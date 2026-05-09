"""Turn-grouping chunker for session transcripts.

Standard recursive splitters break sessions mid-turn, producing chunks like
"…retrieval-augmented genera" / "tion is when…" — incoherent for retrieval.
``turn_grouping_chunker`` parses the marker-delimited transcript produced by
``domains.sessions.sources.SessionsSource``, then greedily packs consecutive
turns into chunks bounded by ``max_tokens``, with ``overlap_turns`` carryover
between adjacent windows so a question and its answer don't get split across
chunks without context.
"""

import re
from dataclasses import dataclass

from domains.sessions.sources import TURN_MARKER_PREFIX

from .types import Chunk

# Match a turn marker line: <<<TURN role=user ts=...>>>
_TURN_RE = re.compile(
    rf"^{re.escape(TURN_MARKER_PREFIX)} role=(?P<role>\S+) ts=(?P<ts>\S+)>>>$",
    re.MULTILINE,
)

# Char-per-token ratio matching retrievers.chunking.registry._CHARS_PER_TOKEN.
_CHARS_PER_TOKEN = 4

DEFAULT_OVERLAP_TURNS = 2


@dataclass(frozen=True)
class _Turn:
    role: str
    ts: str
    content: str

    def serialize(self) -> str:
        return f"{TURN_MARKER_PREFIX} role={self.role} ts={self.ts}>>>\n{self.content}"

    def char_len(self) -> int:
        return len(self.serialize())


def turn_grouping_chunker(
    text: str,
    max_tokens: int = 800,
    overlap_turns: int = DEFAULT_OVERLAP_TURNS,
) -> list[Chunk]:
    """Group consecutive turns into chunks bounded by ``max_tokens``.

    A single turn larger than ``max_tokens`` is emitted alone rather than
    dropped — retrieval correctness over strict size ceiling.
    """
    turns = _parse_turns(text)
    if not turns:
        return []

    max_chars = max_tokens * _CHARS_PER_TOKEN
    chunks: list[Chunk] = []
    window: list[_Turn] = []
    window_chars = 0

    def emit() -> None:
        nonlocal window, window_chars
        if not window:
            return
        chunks.append(
            Chunk(
                text="\n".join(t.serialize() for t in window),
                heading=_window_heading(window),
                index=len(chunks),
            )
        )
        carry = window[-overlap_turns:] if overlap_turns > 0 else []
        window = list(carry)
        window_chars = sum(t.char_len() for t in window)

    for turn in turns:
        cost = turn.char_len()
        if window and window_chars + cost > max_chars:
            emit()
        window.append(turn)
        window_chars += cost

    emit()
    return chunks


def _parse_turns(text: str) -> list[_Turn]:
    matches = list(_TURN_RE.finditer(text))
    if not matches:
        return []
    turns: list[_Turn] = []
    for i, m in enumerate(matches):
        body_start = m.end() + 1
        body_end = matches[i + 1].start() - 1 if i + 1 < len(matches) else len(text)
        content = text[body_start:body_end].rstrip("\n")
        turns.append(_Turn(role=m["role"], ts=m["ts"], content=content))
    return turns


def _window_heading(window: list[_Turn]) -> str:
    first, last = window[0], window[-1]
    if first.ts == last.ts:
        return f"turns {first.ts}"
    return f"turns {first.ts}..{last.ts}"
