"""Wiki-eval judge prompts — loaded from versioned files under prompts/eval/.

Mirrors the synthesis/extraction convention (`workflows.wiki_synthesis.prompts`):
prompt assets live as .md files resolved via KP_PROMPTS_ROOT (default: repo-root
prompts/). Edit the .md to iterate; add a _v2 file for a revision. Each template
carries {placeholder} tokens the judge fills with .format().
"""

import os
from pathlib import Path

_DEFAULT_PROMPTS_ROOT = Path(__file__).resolve().parents[5] / "prompts"
_EVAL_DIR = Path(os.environ.get("KP_PROMPTS_ROOT", _DEFAULT_PROMPTS_ROOT)) / "eval"


def load_prompt(label: str) -> str:
    return (_EVAL_DIR / f"{label}.md").read_text(encoding="utf-8")
