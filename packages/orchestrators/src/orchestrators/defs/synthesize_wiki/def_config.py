# Definition-time config for the synthesize_wiki pipeline.

# Fire at 06:00 UTC — BEFORE the 07:00 sync_wiki_curation tick, so the daily
# synthesis sweep lands in wiki.db before the curation push reads it. Both DAGs
# also share WIKI_WRITE_POOL (bound as the op tag on the assets), which serialises
# them even if synthesis runs long; the offset just keeps them from queueing
# head-to-head every morning.
SCHEDULE_CRON = "0 6 * * *"

# Run-group tag for the synthesis job (UI filtering only — distinct from the
# shared WIKI_WRITE_POOL concurrency key carried on the assets).
JOB_TAG = "synthesize-wiki"

JOB_MAX_RETRIES = "1"
