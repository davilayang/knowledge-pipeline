import dagster as dg

from .assets import all_assets
from .def_config import JOB_MAX_RETRIES, PIPELINE_TAG

fetch_extract_queue_job = dg.define_asset_job(
    name="fetch_extract_queue",
    selection=dg.AssetSelection.assets(*[a.key for a in all_assets]),
    description="One run per Fetching Notion row: fetch then extract then flip lifecycle to Ready.",
    tags={"project": PIPELINE_TAG, "dagster/max_retries": JOB_MAX_RETRIES},
)
