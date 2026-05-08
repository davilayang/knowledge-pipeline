# synthesize_wiki — LLM-powered synthesis of raw_store items into a
# Postgres-backed wiki. See README.md for the runbook.

import dagster as dg

from .assets import all_assets
from .resources import build_resources
from .schedules import synthesize_wiki_job

defs = dg.Definitions(
    assets=all_assets,
    jobs=[synthesize_wiki_job],
    resources=build_resources(),
)
