"""Wiki-synthesis attribute sweep — the unpartitioned pass over queue.db.

Replaces the per-`page_id` partitioned persist: one run reads every source that
has BOTH an `extract_claims` and an `extract_entities` doc in queue.db and, for
each new-or-changed one, synthesises it into wiki.db (source + claims + entity
links). "Changed" is decided by a watermark — a source is re-processed iff its
extraction docs' `max(extracted_at)` has advanced past the `synthesized_at` the
last sweep recorded on it (so a re-extraction re-runs, an unchanged source skips).

Fail-soft per source: one malformed doc is counted and skipped, not allowed to
abort the sweep. The result carries the per-source outcome breakdown the asset
surfaces as metadata (a sweep has no per-partition run history to inspect).
"""

from dataclasses import dataclass, field
from pathlib import Path

from domains.queue_store.sources import get_ready_extraction_docs, get_row
from domains.wiki.attributed import get_synthesized_watermarks
from domains.wiki.state import connection

from workflows.wiki_synthesis.attributed_synthesis import build_source_record, synthesize_source
from workflows.wiki_synthesis.entity_assignment import SubjectMapper


@dataclass(frozen=True)
class SweepResult:
    """Per-source outcome of one attribute sweep, keyed by `content_key` (except
    `partial_extraction`, keyed by `notion_page_id` — those never reached a source
    record). The asset surfaces these counts + the failed keys as metadata."""

    new_sources: list[str] = field(default_factory=list)
    changed_sources: list[str] = field(default_factory=list)
    skipped_unchanged: list[str] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)
    partial_extraction: list[str] = field(default_factory=list)

    @property
    def persisted(self) -> int:
        """Sources written this run — first-time plus re-processed."""
        return len(self.new_sources) + len(self.changed_sources)


def run_attribute_sweep(
    *,
    queue_db_path: Path | str,
    wiki_db_path: Path | str,
    attribute_subjects: SubjectMapper | None = None,
) -> SweepResult:
    """Synthesise every new-or-changed ready source from queue.db into wiki.db.

    A source is READY when it carries both an `extract_claims` and an
    `extract_entities` doc; it is PROCESSED unless its `content_key` already has a
    `synthesized_at` watermark ≥ its docs' `max(extracted_at)` (unchanged → skip).
    Docs + watermark come from one queue.db snapshot (`get_ready_extraction_docs`)
    so a concurrent extraction can't advance the watermark past docs not consumed.
    Sources are DEDUPED by `content_key` — several page_ids sharing a canonical URL
    collapse to the one with the highest watermark, so they can't overwrite each
    other's claims / regress the watermark within a run. `attribute_subjects` is
    passed through to synthesis (None → the production LLM mapper); tests inject a
    deterministic one. Persists nothing on an empty run."""
    ready_docs, partial = get_ready_extraction_docs(db_path=queue_db_path)
    with connection(wiki_db_path) as conn:
        wiki_wm = get_synthesized_watermarks(conn)

    # Collapse page_ids that share a content_key to the freshest one (highest
    # extracted_at). Value: (claims_doc, candidates_doc, extracted_at, source).
    best: dict[str, tuple[str, str, str, object]] = {}
    for page_id, (claims_doc, candidates_doc, extracted_at) in ready_docs.items():
        source = build_source_record(get_row(db_path=queue_db_path, notion_page_id=page_id))
        prev = best.get(source.content_key)
        if prev is None or extracted_at > prev[2]:
            best[source.content_key] = (claims_doc, candidates_doc, extracted_at, source)

    result = SweepResult(partial_extraction=partial)
    for content_key in sorted(best):
        claims_doc, candidates_doc, extracted_at, source = best[content_key]
        prior = wiki_wm.get(content_key)
        if prior is not None and extracted_at <= prior:
            result.skipped_unchanged.append(content_key)
            continue
        try:
            synthesize_source(
                claims_doc=claims_doc,
                candidates_doc=candidates_doc,
                source=source,
                wiki_db_path=wiki_db_path,
                attribute_subjects=attribute_subjects,
                synthesized_at=extracted_at,
            )
        except Exception as exc:  # fail-soft: isolate one bad source
            result.failed[content_key] = f"{type(exc).__name__}: {exc}"
            continue
        (result.changed_sources if prior is not None else result.new_sources).append(content_key)

    return result
