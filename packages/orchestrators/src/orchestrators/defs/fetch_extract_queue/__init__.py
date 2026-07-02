import dagster as dg

from .assets import all_assets
from .checks import all_checks
from .resources import build_resources
from .schedules import fetch_extract_queue_job
from .sensors import all_sensors

# queue.db + Notion only — the wiki-write lane (persist + render) lives in the
# wiki_synthesis DAG now (the store seam).
defs = dg.Definitions(
    assets=all_assets,
    asset_checks=all_checks,
    jobs=[fetch_extract_queue_job],
    sensors=all_sensors,
    resources=build_resources(),
)
