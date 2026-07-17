"""Prompt-file format helper for the `prompts/extraction/` convention.

Each prompt file opens with a design-notes / change-history header, then a
`---` horizontal rule on its own line, then the prompt body. Only the body is
the model-facing instruction — the header records what changed each iteration
and why, and must never reach the model. `strip_design_notes` is applied at
every read site (production loader + eval harness) so what ships equals what's
evaluated.

Mirrors newsletter-assistant's `core.prompts.loader.strip_design_notes` — the
same OS-wide convention (same `\\n---\\n` separator, same passthrough-when-absent
semantics), so a prompt file carved between the two repos behaves identically.
"""

from typing import Final

_NOTES_SEPARATOR: Final[str] = "\n---\n"


def strip_design_notes(text: str) -> str:
    """Return the prompt body, discarding the design-notes header if present.

    The separator must be on its own line (`\\n---\\n`) so horizontal rules
    inside a prompt body aren't mis-detected. A file with no separator is
    body-only and returned unchanged.
    """
    if _NOTES_SEPARATOR not in text:
        return text
    _, body = text.split(_NOTES_SEPARATOR, 1)
    return body.lstrip()
