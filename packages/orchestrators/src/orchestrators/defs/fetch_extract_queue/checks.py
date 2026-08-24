import dagster as dg
from domains.wiki.citations import summarise_citations

from orchestrators.defs.shared.queue_resources import NotionQueueResource, QueueStoreResource

from .def_config import LIFECYCLE_DRIFT_AGE_MINUTES


@dg.asset_check(
    asset=dg.AssetKey(["fetch_extract_queue", "publish_item"]),
    name="notion_lifecycle_in_sync",
    blocking=False,
    description=(
        "Local store rows extracted more than LIFECYCLE_DRIFT_AGE_MINUTES ago "
        "should have Notion Status=Ready. Alerts when the lifecycle-flip leg "
        "is failing silently (extraction landed, Notion never updated)."
    ),
)
def notion_lifecycle_in_sync(
    context: dg.AssetCheckExecutionContext,
    store: QueueStoreResource,
    notion: NotionQueueResource,
) -> dg.AssetCheckResult:
    stale_local = store.list_with_stale_extraction(min_age_minutes=LIFECYCLE_DRIFT_AGE_MINUTES)
    out_of_sync: list[str] = []
    for row in stale_local:
        try:
            status = notion.get_status(row["notion_page_id"])
        except Exception as exc:
            context.log.warning("notion.get_status failed for %s: %s", row["notion_page_id"], exc)
            continue
        if status != "Ready":
            out_of_sync.append(row["notion_page_id"])
    return dg.AssetCheckResult(
        passed=not out_of_sync,
        severity=dg.AssetCheckSeverity.WARN,
        metadata={
            "out_of_sync_count": dg.MetadataValue.int(len(out_of_sync)),
            "out_of_sync_page_ids": dg.MetadataValue.json(out_of_sync[:20]),
            "checked_count": dg.MetadataValue.int(len(stale_local)),
        },
    )


@dg.asset_check(
    asset=dg.AssetKey(["fetch_extract_queue", "extract_claims"]),
    name="claim_citations_hold_up",
    blocking=False,
    description=(
        "Fails when an extracted claim carries a figure or a name the source "
        "never contains — the fabrication the wiki must not accumulate. Also "
        "reports how many claims found their specifics in the units they cited "
        "rather than elsewhere in the source, which measures pointer quality "
        "and is not a fault. Lexical and free — no LLM call. WARN, so a bad "
        "extraction is visible without holding up ingestion."
    ),
)
def claim_citations_hold_up(
    context: dg.AssetCheckExecutionContext,
    store: QueueStoreResource,
) -> dg.AssetCheckResult:
    page_id = context.partition_key
    row = store.get_row(page_id)
    claims_doc = store.get_claims(page_id)
    if not row or not row.get("raw_content") or not claims_doc:
        # extract_claims skips a row with no fetched body, so there is nothing to
        # check rather than something wrong.
        return dg.AssetCheckResult(passed=True, metadata={"skipped": dg.MetadataValue.bool(True)})

    summary = summarise_citations(claims_doc, row["raw_content"])
    return dg.AssetCheckResult(
        passed=summary.failing == 0,
        severity=dg.AssetCheckSeverity.WARN,
        metadata={
            "claims": dg.MetadataValue.int(summary.total),
            "grounded": dg.MetadataValue.int(summary.grounded),
            "localised": dg.MetadataValue.int(summary.localised),
            "localisable": dg.MetadataValue.int(summary.localisable),
            "unchecked": dg.MetadataValue.int(summary.unchecked),
            "uncited": dg.MetadataValue.int(summary.uncited),
            "dangling": dg.MetadataValue.int(summary.dangling),
            "unsupported": dg.MetadataValue.int(summary.unsupported),
            "failing_examples": dg.MetadataValue.json(summary.failing_examples),
            "summary": dg.MetadataValue.md(
                f"**{summary.unsupported} unsupported** of {summary.total} claims "
                f"({summary.uncited} uncited, {summary.dangling} dangling, "
                f"{summary.unchecked} carried no figure to check) — "
                f"{summary.localised}/{summary.localisable} cite a unit that "
                f"contains their specifics"
            ),
        },
    )


all_checks = [notion_lifecycle_in_sync, claim_citations_hold_up]
