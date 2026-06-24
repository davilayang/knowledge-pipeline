# sync_wiki_curation — projects the wiki onto the Notion "Wiki Pages" review
# surface and pulls curator rejections back into the local rejected_entities
# table. See README.md for the DAG diagram and runbook.
#
# Declares only the Notion resource ("wiki_pages_notion"); the "wiki" resource
# is provided by synthesize_wiki and bound at the top-level Definitions.merge
# (re-declaring it would collide on the resource key).

import dagster as dg

from .assets import all_assets
from .resources import build_resources
from .schedules import run_daily_sync_wiki_curation, sync_wiki_curation_job

defs = dg.Definitions(
    assets=all_assets,
    jobs=[sync_wiki_curation_job],
    schedules=[run_daily_sync_wiki_curation],
    resources=build_resources(),
)
