"""Source numbering for cite-by-index grounding.

Ported verbatim-in-behaviour from newsletter-assistant's
`services/agent/src/agent/tools/grounding.py::build_citable_units`. The two must
stay aligned: NA's grounding seam expects kp to become the canonical unit
provider it consumes ("if kp later provides canonical units + stable indices,
this function consumes them instead of re-deriving"). Keeping the split identical
is what makes that swap a no-op. Copied (not imported) because the repos don't
share a package yet; promotion to a shared `domains` unit is a tracked follow-up.

The list index IS the citation ID.
"""

import re

_MAX_UNIT_CHARS = 500  # a unit longer than this won't localise a citation usefully


def _window(text: str, size: int = 320) -> list[str]:
    out: list[str] = []
    cur = ""
    for w in text.split():
        if cur and len(cur) + len(w) + 1 > size:
            out.append(cur)
            cur = ""
        cur = f"{cur} {w}".strip()
    if cur:
        out.append(cur)
    return out


def citable_units(content_md: str) -> list[str]:
    # Naive sentence split (breaks on "Dr."/decimals — harmless for citation
    # resolution). Caption-sourced transcripts can have ~4 periods in 72KB, so
    # sentence-split alone yields giant units — any over-long unit is re-chunked
    # into fixed word windows so a citation still points at a localisable span.
    units: list[str] = []
    for s in re.split(r"(?<=[.!?])\s+", content_md):
        s = s.strip()
        if not s:
            continue
        units.extend(_window(s) if len(s) > _MAX_UNIT_CHARS else [s])
    return units
