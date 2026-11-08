"""The YAML header of newsletter-assistant's note and brief files, read by its own
rule (`core.notes._parse` and `core.briefs._parse`), so the notes and briefs lanes
keep and drop exactly the files newsletter-assistant does."""

from pathlib import Path
from typing import Any

import yaml

_DELIM = "---\n"


def read_header(path: Path) -> dict[str, Any]:
    """The header as a mapping. The file must start with `---\\n` and the header
    ends at the next `\\n---\\n`; an empty header is an empty mapping. Raises
    `ValueError` (or `OSError`, `yaml.YAMLError`) for a file that breaks the rule."""
    text = path.read_text(encoding="utf-8")
    if not text.startswith(_DELIM):
        raise ValueError("missing YAML frontmatter delimiter")
    end = text.find(f"\n{_DELIM}", len(_DELIM))
    if end == -1:
        raise ValueError("unterminated YAML frontmatter")
    header = yaml.safe_load(text[len(_DELIM) : end]) or {}
    if not isinstance(header, dict):
        raise ValueError("YAML frontmatter is not a mapping")
    return header
