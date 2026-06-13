import dagster as dg

from .assets import all_assets
from .def_config import JOB_MAX_RETRIES, PIPELINE_TAG

triage_knowledge_queue_job = dg.define_asset_job(
    name="triage_knowledge_queue",
    selection=dg.AssetSelection.assets(*[a.key for a in all_assets]),
    description=(
        "One run per Queued/empty Notion row: classify, canonicalize, and write "
        "Status=Fetching for fetch_extract_queue to claim."
    ),
    tags={"project": PIPELINE_TAG, "dagster/max_retries": JOB_MAX_RETRIES},
)
