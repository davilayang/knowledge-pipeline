"""synthesize_wiki assets — the wiki-write lane (queue.db docs + notes → wiki.db → pages).

Four assets in a chain, all serialized on the shared wiki-write pool
(WIKI_WRITE_POOL) so synthesis never interleaves with the sync_wiki_curation
writes to the same single-writer wiki.db:

- attribute_claims — an unpartitioned SWEEP over every source in queue.db that
  has both an extract_claims and an extract_entities doc. New-or-changed sources
  (by the synthesized_at watermark) are synthesised into wiki.db; unchanged ones
  skip. Replaces the old per-page_id partitioned persist.
- promote_notes — attaches user-promoted notes (data/notes/*.md, promote: true)
  as `user` claims on the entities their hints resolve to. Runs after
  attribute_claims so hints resolve against the freshest entities.
- render_pages — re-renders every page-worthy entity from wiki.db to data/wiki/.
  Depends on both writers and skips entirely when neither changed anything
  (a no-op render would churn page updated_at and the downstream curation push).
- build_index — rebuilds the resolve.json + index.md sidecars after render.

Reads the shared `wiki` resource (bound in shared.defs) for the wiki.db path +
wiki dir; `queue_store` for queue.db; `notes` for the NA notes dir.
"""

import textwrap

import dagster as dg
from workflows.wiki_synthesis.attributed_synthesis import render_entity_pages
from workflows.wiki_synthesis.promote_notes import promote_notes as run_promote_notes
from workflows.wiki_synthesis.wiki_index import build_wiki_index
from workflows.wiki_synthesis.wiki_sweep import run_attribute_sweep

from orchestrators.config import SYNTHESIZE_WIKI_DAG_VERSION, WIKI_WRITE_POOL
from orchestrators.defs.shared.queue_resources import QueueStoreResource
from orchestrators.defs.shared.resources import NotesResource, WikiResource

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
    key=["synthesize_wiki", "promote_notes"],
    group_name=GROUP_NAME,
    kinds={"sqlite"},
    code_version=SYNTHESIZE_WIKI_DAG_VERSION,
    op_tags=_WIKI_WRITE_TAGS,
    deps=[attribute_claims],
    description=_oneline(
        """
        Attach user-promoted notes (data/notes/*.md with promote: true) to canonical
        wiki entities as `user` claims: resolve each note's relevance-ordered
        `entities` hints (exact-name + alias, alias-aware; a miss mints a `concept`
        entity), drop denylisted hints, then write one user claim per note linked
        to every resolved entity. Idempotent + reconciling — an edited note replaces
        its claim, an unpromoted/deleted one is removed. Runs after attribute_claims
        so hints resolve against the freshest entities. Serialized on the wiki-write
        pool.
        """
    ),
)
def promote_notes(
    context: dg.AssetExecutionContext,
    wiki: WikiResource,
    notes: NotesResource,
) -> int:
    """Returns the dirty count (changed + removed) so render_pages runs when a
    promotion actually changed wiki.db, and skips when nothing did."""
    result = run_promote_notes(
        db_path=wiki.get_db_path(),
        notes_dir=notes.get_notes_dir(),
    )
    if result.fuzzy_hints:
        # Advisory near-miss log (H3): a minted hint that lexically resembles an
        # existing entity — a signal for the next wiki-merge dedup run. Never
        # auto-bound (a false merge is destructive).
        context.log.info(
            "promote_notes: %d fuzzy near-miss hint(s): %s",
            len(result.fuzzy_hints),
            result.fuzzy_hints,
        )
    context.add_output_metadata(
        {
            "notes_promoted": dg.MetadataValue.int(result.written),
            "changed": dg.MetadataValue.int(result.changed),
            "removed": dg.MetadataValue.int(result.removed),
            "fuzzy_hints": dg.MetadataValue.int(len(result.fuzzy_hints)),
            "summary": dg.MetadataValue.md(
                f"Promoted **{result.written}** note(s) "
                f"({result.changed} changed, {result.removed} removed)."
            ),
        }
    )
    return result.dirty


@dg.asset(
    key=["synthesize_wiki", "render_pages"],
    group_name=GROUP_NAME,
    kinds={"sqlite", "file"},
    code_version=SYNTHESIZE_WIKI_DAG_VERSION,
    op_tags=_WIKI_WRITE_TAGS,
    description=_oneline(
        """
        Sweep: re-render every page-worthy entity (≥2 claims OR ≥2 sources, or ≥1
        user note claim) from wiki.db to data/wiki/{slug}-{shortid}.md. Skips
        entirely when neither the attribute_claims sweep nor promote_notes changed
        anything — a no-op render would rewrite every page's updated_at and churn the
        downstream curation push. Serialized on the shared wiki-write pool.
        """
    ),
)
def render_pages(
    context: dg.AssetExecutionContext,
    wiki: WikiResource,
    attribute_claims: int,
    promote_notes: int,
) -> dg.MaterializeResult:
    # The two upstream change-signals: attribute_claims' persisted count and
    # promote_notes' dirty count. Skip the render only when BOTH are zero, so
    # untouched pages keep their updated_at.
    if not attribute_claims and not promote_notes:
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


@dg.asset(
    key=["synthesize_wiki", "build_index"],
    group_name=GROUP_NAME,
    kinds={"sqlite", "file"},
    code_version=SYNTHESIZE_WIKI_DAG_VERSION,
    op_tags=_WIKI_WRITE_TAGS,
    deps=[render_pages],
    description=_oneline(
        """
        Rebuild the whole-wiki index sidecars from wiki.db: data/wiki/_index/resolve.json
        (alias→entity_id resolution + per-entity orientation for the newsletter-assistant
        bridge) and data/wiki/index.md (human TOC). Runs after render_pages and reads
        wiki.db fresh; writes each file only when its content changed (snapshot_id for
        resolve.json, byte-equality for index.md) and self-heals a missing file.
        Serialized on the shared wiki-write pool.
        """
    ),
)
def build_index(context: dg.AssetExecutionContext, wiki: WikiResource) -> dg.MaterializeResult:
    try:
        r = build_wiki_index(wiki_db_path=wiki.get_db_path(), wiki_dir=wiki.get_wiki_dir())
    except ValueError as e:  # alias collision → one lowercased key, two entity_ids
        raise dg.Failure(description=str(e)) from e
    return dg.MaterializeResult(
        metadata={
            "aliases_total": dg.MetadataValue.int(r.aliases_total),
            "pages_total": dg.MetadataValue.int(r.pages_total),
            "resolve_written": dg.MetadataValue.bool(r.resolve_written),
            "index_written": dg.MetadataValue.bool(r.index_written),
            "summary": dg.MetadataValue.md(
                f"Index: **{r.pages_total}** pages, **{r.aliases_total}** alias keys "
                f"(resolve.json {'written' if r.resolve_written else 'unchanged'}, "
                f"index.md {'written' if r.index_written else 'unchanged'})."
            ),
        }
    )


all_assets = [attribute_claims, promote_notes, render_pages, build_index]
