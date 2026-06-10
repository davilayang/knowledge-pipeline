import dagster as dg

from .assets import all_assets
from .def_config import JOB_MAX_RETRIES, PIPELINE_TAG

triage_queued_items_job = dg.define_asset_job(
    name="triage_queued_items",
    selection=dg.AssetSelection.assets(*[a.key for a in all_assets]),
    description=(
        "One run per Queued/empty Notion row: classify, canonicalize, and write "
        "Status=Fetching for extract_complex_contents to claim."
    ),
    tags={"project": PIPELINE_TAG, "dagster/max_retries": JOB_MAX_RETRIES},
)
