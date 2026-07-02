import dagster as dg

from .assets import all_assets, render_attributed_pages
from .checks import all_checks
from .resources import build_resources
from .schedules import fetch_extract_queue_job, render_attributed_pages_job
from .sensors import all_sensors

# render_attributed_pages (unpartitioned) is registered alongside the partitioned
# per-source assets; the "wiki" resource it (and persist_attributed_claims) needs
# is provided by synthesize_wiki.defs at the top-level Definitions.merge.
defs = dg.Definitions(
    assets=[*all_assets, render_attributed_pages],
    asset_checks=all_checks,
    jobs=[fetch_extract_queue_job, render_attributed_pages_job],
    sensors=all_sensors,
    resources=build_resources(),
)
