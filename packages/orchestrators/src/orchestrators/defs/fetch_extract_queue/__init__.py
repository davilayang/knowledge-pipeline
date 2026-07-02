import dagster as dg

from .assets import all_assets, render_attributed_pages
from .checks import all_checks
from .resources import build_resources
from .schedules import (
    fetch_extract_queue_job,
    render_attributed_pages_job,
    render_attributed_pages_schedule,
)
from .sensors import all_sensors

# render_attributed_pages (unpartitioned) is registered alongside the partitioned
# per-source assets; its wiki-write access comes from this pipeline's own
# `wiki_write` resource (build_resources), and a daily schedule sweeps it.
defs = dg.Definitions(
    assets=[*all_assets, render_attributed_pages],
    asset_checks=all_checks,
    jobs=[fetch_extract_queue_job, render_attributed_pages_job],
    schedules=[render_attributed_pages_schedule],
    sensors=all_sensors,
    resources=build_resources(),
)
