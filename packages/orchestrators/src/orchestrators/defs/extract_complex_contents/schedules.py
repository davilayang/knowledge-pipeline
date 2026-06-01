import dagster as dg

from .assets import all_assets
from .def_config import JOB_MAX_RETRIES, PIPELINE_TAG

extract_complex_contents_job = dg.define_asset_job(
    name="extract_complex_contents",
    selection=dg.AssetSelection.assets(*[a.key for a in all_assets]),
    description="One run per Fetching Notion row: fetch then extract then flip lifecycle to Ready.",
    tags={"project": PIPELINE_TAG, "dagster/max_retries": JOB_MAX_RETRIES},
)
