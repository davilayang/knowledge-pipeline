"""Pure helpers for turning LLM page output into a WikiPage.

Frontmatter parsing, enforcement of the expected entity_id/page_type, and the
H2 preservation warning. Tolerates two common LLM output defects: a wrapping
```yaml/```markdown code fence (stripped before parsing) and malformed
frontmatter YAML (summary/title recovered by regex rather than lost).
"""

import logging
import re
from datetime import date
from pathlib import Path

import yaml
from domains.wiki.types import WikiPage

logger = logging.getLogger(__name__)


_LLM_ACCEPTED_FIELDS = frozenset(
    {"entity_id", "title", "page_type", "related", "sources", "summary"}
)


_PRODUCER_FRONTMATTER_KEYS = ("aliases", "related", "sources", "num_sources", "updated_at")


def strip_producer_frontmatter(page_text: str) -> str:
    """Drop producer-owned frontmatter keys from a rendered page before it is
    fed back to the synthesis LLM on update (#54).

    `aliases`, `related`, `sources`, `num_sources`, `updated_at` are derived by
    the producer from the wiki.db ledgers, not authored by the LLM. Showing them
    back risks the model echoing volatile or accumulated metadata into the body
    (which, for an accumulated `related`/`sources`, would also churn the #47
    version hash if it leaked into `content`). LLM-authored fields
    (entity_id/title/page_type/summary) and the body are kept untouched. Only the
    leading frontmatter block is touched; a body line like `sources: …` is safe.
    """
    m = re.match(r"^---\r?\n(.*?)\r?\n---\r?\n", page_text, re.DOTALL)
    if not m:
        return page_text
    kept = [
        line
        for line in m.group(1).splitlines()
        if line.split(":", 1)[0].strip() not in _PRODUCER_FRONTMATTER_KEYS
    ]
    return "---\n" + "\n".join(kept) + "\n---\n" + page_text[m.end() :]


def _strip_code_fence(text: str) -> str:
    """Drop a wrapping ```lang ... ``` fence the LLM sometimes emits around its
    whole response. Without this the fence bypasses frontmatter parsing and the
    block leaks into the summary."""
    if not text.startswith("```"):
        return text
    nl = text.find("\n")
    if nl == -1:
        return text
    inner = text[nl + 1 :]
    if inner.rstrip().endswith("```"):
        inner = inner.rstrip()[:-3]
    return inner.strip()


def _field_from_frontmatter_text(yaml_str: str, field: str) -> str:
    """Pull a single `field: value` line out of frontmatter text by regex.

    Used to recover the summary/title when YAML parsing fails (e.g. an unquoted
    title containing a colon), instead of dumping the whole block into the
    summary as first-sentence junk.
    """
    m = re.search(rf"^{field}:\s*(.+)$", yaml_str, re.MULTILINE)
    if not m:
        return ""
    val = m.group(1).strip()
    if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
        val = val[1:-1]
    return val.strip()


def _first_sentence(text: str) -> str:
    """Return the first sentence of `text`, stripped of markdown headings."""
    cleaned_lines: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        cleaned_lines.append(stripped)
    if not cleaned_lines:
        return ""
    flat = " ".join(cleaned_lines)
    for i, ch in enumerate(flat):
        if ch in ".!?":
            return flat[: i + 1].strip()
    return flat.strip()


def parse_llm_page_output(
    *,
    raw: str,
    entity_id: str,
    title: str,
    page_type: str,
    related: list[str],
    source_id: str,
) -> WikiPage:
    """Parse LLM output into a WikiPage, falling back to defaults for bad frontmatter.

    LLMs sometimes hallucinate a different entity_id or page_type — we always
    overwrite those with what the caller asked for to keep the page index stable.

    Only fields in `_LLM_ACCEPTED_FIELDS` are consumed from the LLM frontmatter.
    Producer-supplied fields (aliases, num_sources) are intentionally ignored
    even if the LLM emits them — they're authoritative from wiki.db at write
    time, not from the LLM.
    """
    raw = _strip_code_fence(raw.strip())

    if raw.startswith("---"):
        rest = raw[3:]
        end = rest.find("\n---")
        if end != -1:
            yaml_str = rest[:end]
            content = rest[end + 4 :].strip()
            try:
                meta = yaml.safe_load(yaml_str)
                if isinstance(meta, dict):
                    llm_entity_id = meta.get("entity_id", entity_id)
                    if llm_entity_id != entity_id:
                        logger.warning(
                            "LLM returned entity_id '%s' but expected '%s', using expected",
                            llm_entity_id,
                            entity_id,
                        )
                    rejected = sorted(set(meta) - _LLM_ACCEPTED_FIELDS)
                    if rejected:
                        logger.warning(
                            "LLM emitted non-accepted frontmatter fields for %s: %s",
                            entity_id,
                            rejected,
                        )
                    summary = meta.get("summary", "")
                    if not isinstance(summary, str):
                        summary = ""
                    summary = summary.strip()
                    if not summary:
                        summary = _first_sentence(content)
                        logger.warning(
                            "LLM did not emit a usable summary for %s; "
                            "falling back to first sentence",
                            entity_id,
                        )
                    return WikiPage(
                        entity_id=entity_id,
                        title=meta.get("title", title),
                        page_type=page_type,
                        summary=summary,
                        related=meta.get("related", related),
                        sources=meta.get("sources", [source_id]),
                        updated_at=date.today(),
                        content=content,
                    )
            except (yaml.YAMLError, ValueError):
                logger.warning(
                    "Bad frontmatter YAML from LLM for %s; recovering fields by regex",
                    entity_id,
                )
                summary = _field_from_frontmatter_text(yaml_str, "summary") or _first_sentence(
                    content
                )
                return WikiPage(
                    entity_id=entity_id,
                    title=_field_from_frontmatter_text(yaml_str, "title") or title,
                    page_type=page_type,
                    summary=summary,
                    related=related,
                    sources=[source_id],
                    updated_at=date.today(),
                    content=content,
                )

    return WikiPage(
        entity_id=entity_id,
        title=title,
        page_type=page_type,
        summary=_first_sentence(raw),
        related=related,
        sources=[source_id],
        updated_at=date.today(),
        content=raw,
    )


def check_h2_preservation(page_path: Path, new_content: str) -> None:
    """Warn if existing H2 sections were dropped in the merge."""
    old_text = page_path.read_text(encoding="utf-8")
    old_h2s = set(re.findall(r"^## (.+)$", old_text, re.MULTILINE))
    new_h2s = set(re.findall(r"^## (.+)$", new_content, re.MULTILINE))

    dropped = old_h2s - new_h2s
    if dropped:
        logger.warning(
            "Page %s: H2 sections dropped in merge: %s",
            page_path.name,
            ", ".join(sorted(dropped)),
        )
