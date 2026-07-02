# wiki_synthesis — the wiki-write lane carved out of fetch_extract_queue. The DAG
# boundary is the store seam: everything that writes wiki.db lives here; the
# queue.db + Notion path stays in fetch_extract_queue. See README.md.
#
# Declares its own `queue_store` (queue.db reader) — a pipeline-scoped key, so it
# doesn't collide with fetch_extract_queue's `store` (Dagster's merge forbids two
# sub-Definitions binding the same key), mirroring triage's `triage_store`. The
# `wiki` resource is provided by shared.defs and bound at the top-level merge.

import dagster as dg

from orchestrators.defs.shared.queue_resources import QueueStoreResource

from .assets import all_assets
from .schedules import run_daily_wiki_synthesis, wiki_synthesis_job

defs = dg.Definitions(
    assets=all_assets,
    jobs=[wiki_synthesis_job],
    schedules=[run_daily_wiki_synthesis],
    resources={"queue_store": QueueStoreResource()},
)
