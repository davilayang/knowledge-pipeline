# Asset job + daily schedule for synthesize_wiki.
#
# Schedule fires at 06:00 UTC, reads raw_store ∖ wiki.processed at fire time,
# and yields one RunRequest with the pending item_ids in run_config. One
# scheduled tick = one Dagster run = full pending → synthesized → index cycle.

import dagster as dg
import psycopg
from domains.wiki.sources import RawStoreSource
from domains.wiki.state import get_processed_ids

from .assets import all_assets, synthesized
from .def_config import (
    JOB_MAX_RETRIES,
    MAX_PER_TICK_DEFAULT,
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


def _discover_pending(wiki: WikiResource, max_per_tick: int) -> list[str]:
    raw_ids = RawStoreSource(wiki.get_raw_store_path()).get_item_ids()
    with psycopg.connect(wiki.database_url) as conn:
        done = get_processed_ids(conn, status="ok")
        skipped = get_processed_ids(conn, status="skipped")
    handled = done | skipped
    pending = [r for r in raw_ids if r not in handled]
    if max_per_tick > 0:
        pending = pending[:max_per_tick]
    return pending


@dg.schedule(
    cron_schedule=SCHEDULE_CRON,
    job=synthesize_wiki_job,
    required_resource_keys={"wiki"},
)
def run_daily_synthesize_wiki(
    context: dg.ScheduleEvaluationContext,
) -> dg.RunRequest | dg.SkipReason:
    wiki: WikiResource = context.resources.wiki
    pending = _discover_pending(wiki, MAX_PER_TICK_DEFAULT)
    if not pending:
        return dg.SkipReason("no new raw_store items since last run")

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
