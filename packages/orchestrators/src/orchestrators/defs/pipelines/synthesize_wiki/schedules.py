# Asset job for synthesize_wiki. No schedule — pipeline is manually triggered
# (cost-aware LLM batch). The named job exists so operators can launch the
# whole pipeline (`dg launch --job synthesize_wiki`) and the UI groups runs
# by job name.

import dagster as dg

from .assets import all_assets
from .def_config import JOB_MAX_RETRIES, PIPELINE_TAG

synthesize_wiki_job = dg.define_asset_job(
    name="synthesize_wiki",
    description=(
        "LLM synthesis of raw_store items into structured wiki pages "
        "(concept/tool/trend) backed by Postgres. Manual trigger; per-doc "
        "checkpointing via LangGraph means partition retries are cheap."
    ),
    selection=dg.AssetSelection.assets(*[a.key for a in all_assets]),
    tags={
        "project": PIPELINE_TAG,
        "dagster/max_retries": JOB_MAX_RETRIES,
    },
)


__all__ = ["synthesize_wiki_job"]
