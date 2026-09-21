import re

import dagster as dg
from domains.content_urls import FIGURE_GATED_TYPES

from orchestrators.defs.shared.queue_resources import NotionQueueResource, QueueStoreResource

from .def_config import LIFECYCLE_DRIFT_AGE_MINUTES

# An unresolved figure anchor as the converter emits it, with the caption that
# follows it on the next block. The caption may sit inside a callout, so the
# blockquote marker is optional. A described figure has had its anchor replaced
# by the description, so an anchor still present IS an undescribed figure.
_FIGURE = re.compile(
    r"!\[figure\]\((?P<anchor>oreilly:[^)]+)\)\s*\n+(?:> )?\*\*(?P<caption>[^*]+)\*\*"
)
_ANCHOR = re.compile(r"!\[figure\]\((?P<anchor>oreilly:[^)]+)\)")


def figure_repair_template(markdown: str) -> dict[str, dict[str, str]]:
    """Every undescribed figure in `markdown`, keyed by the anchor an injector
    will match on, with the caption beside it so an operator can tell which
    picture is which.

    Keyed by the full anchor rather than a short alias: the key is what the
    description has to be matched back to, and an alias would reintroduce the
    mapping step the template exists to remove.
    """
    captions = {m.group("anchor"): m.group("caption").strip() for m in _FIGURE.finditer(markdown)}
    return {
        anchor: {"caption": captions.get(anchor, ""), "description": ""}
        for anchor in dict.fromkeys(m.group("anchor") for m in _ANCHOR.finditer(markdown))
    }


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


def figure_gate_result(content_type: str, raw_content: str) -> dg.AssetCheckResult:
    """The gate's decision for one row. Separate from the check so it can be
    exercised directly: a partitioned `@asset_check` cannot be invoked without
    a run, and this is where every decision is made."""
    if (content_type or "") not in FIGURE_GATED_TYPES:
        return dg.AssetCheckResult(passed=True, metadata={"gated": dg.MetadataValue.bool(False)})

    template = figure_repair_template(raw_content or "")
    if not template:
        return dg.AssetCheckResult(
            passed=True,
            metadata={"gated": dg.MetadataValue.bool(True), "figures": dg.MetadataValue.int(0)},
        )
    # The template rides in check metadata, not in the exception: the run-failure
    # handler copies a message into Notion's Error, which truncates at 1,900
    # characters and would cut a template of any size in half.
    return dg.AssetCheckResult(
        passed=False,
        severity=dg.AssetCheckSeverity.ERROR,
        metadata={
            "gated": dg.MetadataValue.bool(True),
            "figures": dg.MetadataValue.int(len(template)),
            "figure_text_template": dg.MetadataValue.json(template),
            "summary": dg.MetadataValue.md(
                f"**{len(template)} figures need a description.** Fill the "
                f"`figure_text_template` below and re-queue the row."
            ),
        },
    )


@dg.asset_check(
    asset=dg.AssetKey(["fetch_extract_queue", "fetch_content"]),
    name="book_chapter_figures_described",
    blocking=True,
    description=(
        "A book chapter whose figures carry no description parks at "
        "Status=Failed rather than reaching extraction, because a chapter "
        "whose substance is pictorial must not enter the corpus looking "
        "complete. The failure carries a repair template naming every figure."
    ),
)
def book_chapter_figures_described(
    context: dg.AssetCheckExecutionContext,
    store: QueueStoreResource,
) -> dg.AssetCheckResult:
    row = store.get_row(context.partition_key) or {}
    return figure_gate_result(row.get("content_type") or "", row.get("raw_content") or "")


all_checks = [notion_lifecycle_in_sync, book_chapter_figures_described]
