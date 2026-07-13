"""Layered extraction — chunk the numbered source, extract per chunk, merge.

A single wide extraction pass over a long document grounds ~nothing in its final
deciles (the `coverage` metric exposes this tail-starvation). Layered extraction
fixes it by chunking: split the document into windows, extract from each,
concatenate the claims.

The load-bearing trick is that units are numbered **once, globally**, and every
chunk keeps its units' global `[i]` indices. So a claim a late chunk emits cites
a global tail index, and the merged output drops straight into `coverage(units,
claims)` against the same global `citable_units(content)` — no index remapping.

Merge here is plain concatenation. Semantic dedup (embed the claims, cluster by
cosine, keep one per cluster) is deferred: chunk overlap produces duplicate
claims, but the `coverage` `redundancy` count measures whether they actually
hurt before that cost is paid.
"""

from collections.abc import Callable
from typing import NamedTuple

from evals.extraction.units import citable_units
from evals.extraction.wide import WideOutput


class Chunk(NamedTuple):
    start: int  # global index of this chunk's first unit
    units: list[str]


def chunk_units(units: list[str], *, budget_chars: int, overlap_units: int = 0) -> list[Chunk]:
    """Pack contiguous units into windows of ~budget_chars, each tagged with its
    global start index. overlap_units re-includes that many trailing units of the
    previous chunk so a claim split across a boundary isn't lost."""
    chunks: list[Chunk] = []
    n = len(units)
    i = 0
    while i < n:
        j, size = i, 0
        while j < n and (j == i or size + len(units[j]) <= budget_chars):
            size += len(units[j])  # first unit always taken (avoids stall on an oversized unit)
            j += 1
        chunks.append(Chunk(i, units[i:j]))
        if j >= n:
            break
        i = max(j - overlap_units, i + 1)  # rewind for overlap; +1 guarantees progress
    return chunks


def render_chunk(chunk: Chunk) -> str:
    """Number the chunk's units with their global `[i]` index — what the model cites."""
    return "\n".join(f"[{chunk.start + k}] {u}" for k, u in enumerate(chunk.units))


# chunk_extract_fn(numbered_chunk_text) -> (WideOutput dict, tokens_in, tokens_out).
# Injected seam — tests stub it, runtime wires the OpenAI per-chunk call.
ChunkExtractFn = Callable[[str], tuple[dict, int, int]]


def make_layered_extract_fn(
    *,
    chunk_extract_fn: ChunkExtractFn,
    budget_chars: int,
    overlap_units: int = 0,
) -> Callable[[str], tuple[dict, int, int]]:
    """Build an `ExtractFn` (`content -> (WideOutput dict, tin, tout)`) that chunks
    the globally-numbered source, extracts per chunk, and concatenates the claims.

    Drop-in for `make_wide_variant(extract_fn=...)`: because units are numbered once
    globally and each chunk keeps those indices, the merged claims align with
    `citable_units(content)` and feed `coverage()` unchanged.
    """

    def _extract(content: str) -> tuple[dict, int, int]:
        units = citable_units(content)
        chunks = chunk_units(units, budget_chars=budget_chars, overlap_units=overlap_units)
        claims: list[dict] = []
        title = ""
        tin = tout = 0
        for chunk in chunks:
            out, ti, to = chunk_extract_fn(render_chunk(chunk))
            tin += ti
            tout += to
            title = title or out.get("extracted_title", "")
            claims.extend(out.get("claims", []))
        return WideOutput(extracted_title=title, claims=claims).model_dump(), tin, tout

    return _extract


def openai_chunk_extract_fn(
    *, api_key: str, model: str, prompt_text: str, max_tokens: int = 4096
) -> ChunkExtractFn:
    """Runtime per-chunk seam: sends the already-globally-numbered chunk text to an
    OpenAI structured-output call. Unlike `openai_wide_extract_fn` it does NOT
    number the source — `make_layered_extract_fn` numbers globally first, so the
    model sees (and cites) global `[i]` indices. Untested I/O boundary.
    """
    import openai

    client = openai.OpenAI(api_key=api_key)

    def _extract(numbered_chunk: str) -> tuple[dict, int, int]:
        resp = client.beta.chat.completions.parse(
            model=model,
            max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": prompt_text},
                {"role": "user", "content": numbered_chunk},
            ],
            response_format=WideOutput,
        )
        usage = resp.usage
        return (
            resp.choices[0].message.parsed.model_dump(),
            usage.prompt_tokens if usage else 0,
            usage.completion_tokens if usage else 0,
        )

    return _extract
