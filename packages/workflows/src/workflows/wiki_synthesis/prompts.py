# Wiki synthesis prompts — loaded from versioned files under prompts/wiki/.
#
# Mirrors the extraction (prompts/extraction/) and triage (prompts/triage/)
# convention: prompt assets live as .md files resolved via KP_PROMPTS_ROOT
# (default: repo-root prompts/). Edit the .md to iterate; add a _v2 file for a
# revision. The USER templates carry {placeholder} tokens that synthesize.py
# fills with .format(); the page-synthesis USER templates lead with the shared
# article block for prompt caching (see the files).

import os
from pathlib import Path

_DEFAULT_PROMPTS_ROOT = Path(__file__).resolve().parents[5] / "prompts"
_WIKI_DIR = Path(os.environ.get("KP_PROMPTS_ROOT", _DEFAULT_PROMPTS_ROOT)) / "wiki"


def _load(label: str) -> str:
    return (_WIKI_DIR / f"{label}.md").read_text(encoding="utf-8")


ENTITY_EXTRACTION_SYSTEM = _load("entity_extraction_system_v1")
ENTITY_EXTRACTION_USER = _load("entity_extraction_user_v1")
# Extract-time shared-prefix trio: both the claims call and the entities call send
# EXTRACT_SHARED_SYSTEM + the EXTRACT_ARTICLE_ENVELOPE (byte-identical, so the
# article prompt-caches across the two calls); only the task tail differs.
EXTRACT_SHARED_SYSTEM = _load("extract_shared_system_v1")
EXTRACT_ARTICLE_ENVELOPE = _load("extract_article_envelope_v1")
EXTRACT_CLAIMS_TASK = _load("extract_claims_task_v1")
EXTRACT_ENTITIES_TASK = _load("extract_entities_task_v1")
PAGE_SYNTHESIS_SYSTEM = _load("page_synthesis_system_v1")
PAGE_SYNTHESIS_USER_CREATE = _load("page_synthesis_user_create_v1")
PAGE_SYNTHESIS_USER_UPDATE = _load("page_synthesis_user_update_v1")
SUBJECT_ATTRIBUTION_SYSTEM = _load("subject_attribution_system_v1")
SUBJECT_ATTRIBUTION_USER = _load("subject_attribution_user_v1")
