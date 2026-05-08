# Asset job + daily schedule for synthesize_wiki.
#
# Schedule fires at 06:00 UTC, reads the newest backup_readings snapshot of
# raw_store ∖ wiki.processed at fire time, and yields one RunRequest with the
# pending item_ids in run_config. One scheduled tick = one Dagster run = full
# pending → synthesized → index cycle.

from datetime import date

import dagster as dg
import psycopg
from domains.wiki.sources import RawStoreSource
from domains.wiki.state import get_processed_ids

from .assets import all_assets, synthesized
from .def_config import (
    JOB_MAX_RETRIES,
    MAX_PER_TICK_DEFAULT,
    MAX_SNAPSHOT_AGE_DAYS,
    PIPELINE_TAG,
    SCHEDULE_CRON,
    SOURCE_RAW_STORE,
)
from .resources import WikiResource

synthesize_wiki_job = dg.define_asset_job(
    name="synthesize_wiki",
    description=(
        "Daily LLM synthesis of pending raw_store items into structured "
        "wiki pages (concept/tool/trend) backed by Postgres, then "
        "regenerate the wiki index."
    ),
    selection=dg.AssetSelection.assets(*[a.key for a in all_assets]),
    tags={
        "project": PIPELINE_TAG,
        "dagster/max_retries": JOB_MAX_RETRIES,
    },
)


def _discover_pending(
    wiki: WikiResource, max_per_tick: int
) -> tuple[list[str], str] | tuple[None, str]:
    """Return (pending_ids, snapshot_date_iso) or (None, skip_reason)."""
    snapshot = wiki.latest_raw_store_snapshot()
    if snapshot is None:
        return None, f"no raw_store snapshot found under {wiki.backup_dir}"
    snapshot_path, snapshot_date = snapshot
    age_days = (date.today() - snapshot_date).days
    if age_days > MAX_SNAPSHOT_AGE_DAYS:
        return None, (
            f"newest raw_store snapshot is {snapshot_date.isoformat()} "
            f"({age_days} days old, limit {MAX_SNAPSHOT_AGE_DAYS})"
        )

    raw_ids = RawStoreSource(snapshot_path).get_item_ids()
    with psycopg.connect(wiki.database_url) as conn:
        done = get_processed_ids(conn, status="ok")
        skipped = get_processed_ids(conn, status="skipped")
    handled = done | skipped
    pending = [r for r in raw_ids if r not in handled]
    if max_per_tick > 0:
        pending = pending[:max_per_tick]
    return pending, snapshot_date.isoformat()


@dg.schedule(
    cron_schedule=SCHEDULE_CRON,
    job=synthesize_wiki_job,
    required_resource_keys={"wiki"},
)
def run_daily_synthesize_wiki(
    context: dg.ScheduleEvaluationContext,
) -> dg.RunRequest | dg.SkipReason:
    wiki: WikiResource = context.resources.wiki
    pending, info = _discover_pending(wiki, MAX_PER_TICK_DEFAULT)
    if pending is None:
        return dg.SkipReason(info)
    if not pending:
        return dg.SkipReason(f"no new raw_store items since last run (snapshot {info})")

    partition = context.scheduled_execution_time.date().isoformat()
    return dg.RunRequest(
        run_key=partition,
        partition_key=partition,
        run_config={
            "ops": {
                # Op name is derived from the asset key (wiki/synthesized →
                # wiki__synthesized), not the function name.
                synthesized.op.name: {
                    "config": {
                        "item_ids": pending,
                        "source_type": SOURCE_RAW_STORE,
                    }
                }
            }
        },
    )


__all__ = ["synthesize_wiki_job", "run_daily_synthesize_wiki"]
