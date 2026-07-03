"""synthesize_wiki assets — the wiki-write lane (queue.db docs → wiki.db → pages).

Two assets, both serialized on the shared wiki-write pool (WIKI_WRITE_POOL) so
synthesis never interleaves with the sync_wiki_curation writes to the same
single-writer wiki.db:

- attribute_claims — an unpartitioned SWEEP over every source in queue.db that
  has both an extract_claims and an extract_entities doc. New-or-changed sources
  (by the synthesized_at watermark) are synthesised into wiki.db; unchanged ones
  skip. Replaces the old per-page_id partitioned persist.
- render_pages — re-renders every page-worthy entity from wiki.db to data/wiki/.
  Depends on attribute_claims and skips entirely when the sweep changed nothing
  (a no-op render would churn page updated_at and the downstream curation push).

Reads the shared `wiki` resource (bound in shared.defs) for the wiki.db path +
wiki dir; the `store` resource for queue.db.
"""

import textwrap

import dagster as dg
from workflows.wiki_synthesis.attributed_synthesis import render_entity_pages
from workflows.wiki_synthesis.wiki_sweep import run_attribute_sweep

from orchestrators.config import SYNTHESIZE_WIKI_DAG_VERSION, WIKI_WRITE_POOL
from orchestrators.defs.shared.queue_resources import QueueStoreResource
from orchestrators.defs.shared.resources import WikiResource

GROUP_NAME = "synthesize_wiki"

# Wiki.db is single-writer; both assets carry the shared WIKI_WRITE_POOL op tag so
# a synthesis write can never run concurrently with a curation write (or, on a
# large sweep, with itself). See config.WIKI_WRITE_POOL + configs/dagster.yaml.
_WIKI_WRITE_TAGS = {"dagster/concurrency_key": WIKI_WRITE_POOL}


def _oneline(s: str) -> str:
    return " ".join(textwrap.dedent(s).split())


@dg.asset(
    key=["synthesize_wiki", "attribute_claims"],
    group_name=GROUP_NAME,
    kinds={"sqlite"},
    code_version=SYNTHESIZE_WIKI_DAG_VERSION,
    op_tags=_WIKI_WRITE_TAGS,
    description=_oneline(
        """
        Unpartitioned sweep: for every queue.db source with both an extract_claims
        and an extract_entities doc, synthesise the new-or-changed ones into wiki.db
        (source + claims + claim→entity links, mint-or-match entities). "Changed" is
        decided by the synthesized_at watermark (max extracted_at consumed); a
        re-extracted source is re-processed with its claims replaced. Fail-soft per
        source. Serialized on the shared wiki-write pool.
        """
    ),
)
def attribute_claims(
    context: dg.AssetExecutionContext,
    queue_store: QueueStoreResource,
    wiki: WikiResource,
) -> int:
    """Returns the persisted count so render_pages can skip an empty sweep. Rich
    per-source outcome rides through as output metadata (a sweep has no
    per-partition run to inspect)."""
    result = run_attribute_sweep(
        queue_db_path=queue_store.get_db_path(),
        wiki_db_path=wiki.get_db_path(),
    )
    if result.failed:
        context.log.warning(
            "attribute_claims: %d source(s) failed: %s", len(result.failed), result.failed
        )
    context.add_output_metadata(
        {
            "persisted": dg.MetadataValue.int(result.persisted),
            "new_sources": dg.MetadataValue.int(len(result.new_sources)),
            "changed_reprocessed": dg.MetadataValue.int(len(result.changed_sources)),
            "skipped_unchanged": dg.MetadataValue.int(len(result.skipped_unchanged)),
            "failed": dg.MetadataValue.int(len(result.failed)),
            "partial_extraction": dg.MetadataValue.int(len(result.partial_extraction)),
            "failed_keys": dg.MetadataValue.json(result.failed),
            "summary": dg.MetadataValue.md(
                f"Persisted **{result.persisted}** "
                f"({len(result.new_sources)} new, {len(result.changed_sources)} re-processed); "
                f"skipped {len(result.skipped_unchanged)}, failed {len(result.failed)}."
            ),
        }
    )
    return result.persisted


@dg.asset(
    key=["synthesize_wiki", "render_pages"],
    group_name=GROUP_NAME,
    kinds={"sqlite", "file"},
    code_version=SYNTHESIZE_WIKI_DAG_VERSION,
    op_tags=_WIKI_WRITE_TAGS,
    description=_oneline(
        """
        Sweep: re-render every page-worthy entity (≥2 claims OR ≥2 sources) from
        wiki.db to data/wiki/{slug}-{shortid}.md. Skips entirely when the upstream
        attribute_claims sweep changed nothing — a no-op render would rewrite every
        page's updated_at and churn the downstream curation push. Serialized on the
        shared wiki-write pool.
        """
    ),
)
def render_pages(
    context: dg.AssetExecutionContext,
    wiki: WikiResource,
    attribute_claims: int,
) -> dg.MaterializeResult:
    # attribute_claims is the upstream sweep's persisted count — skip the render
    # when nothing changed so untouched pages keep their updated_at.
    if not attribute_claims:
        return dg.MaterializeResult(
            metadata={
                "pages_written": dg.MetadataValue.int(0),
                "summary": dg.MetadataValue.md(
                    "Empty sweep — no render (page timestamps unchanged)."
                ),
            }
        )
    written = render_entity_pages(
        wiki_db_path=wiki.get_db_path(),
        wiki_dir=wiki.get_wiki_dir(),
    )
    return dg.MaterializeResult(
        metadata={
            "pages_written": dg.MetadataValue.int(len(written)),
            "summary": dg.MetadataValue.md(f"Rendered {len(written)} attributed pages"),
        }
    )


all_assets = [attribute_claims, render_pages]
