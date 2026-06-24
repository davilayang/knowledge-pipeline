# Definition-time config for the sync_wiki_curation pipeline.

from orchestrators.defs.synthesize_wiki.def_config import PIPELINE_TAG

# SQLite is single-writer. This DAG and synthesize_wiki MUST share ONE
# concurrency key so a curation delete can never run concurrently with a
# synthesis persist against the same wiki.db (a mid-flight persist could
# FK-error against a concurrent entity delete). We bind synthesize_wiki's own
# key here — NOT a separate per-DAG key — which is the single most important
# correctness control for this pipeline.
#
# Load-bearing dependency: the serialisation only holds because
# configs/dagster.yaml sets `concurrency.pools.{granularity: op, default_limit: 1}`,
# which caps this shared pool at one concurrent op globally. Raising default_limit
# (or setting an explicit higher limit on this pool) silently breaks the
# single-writer guarantee with no test failing — keep that config in lockstep.
WIKI_DB_CONCURRENCY_KEY = PIPELINE_TAG

# Run-group tag for the curation job (UI filtering only — distinct from the
# shared concurrency key above).
JOB_TAG = "sync-wiki-curation"

# Fire at 07:00 UTC — after the 06:00 synthesis tick. The shared concurrency key
# still serialises the two if synthesis runs long; the offset just keeps them
# from queueing head-to-head every morning. Curation isn't latency-sensitive.
SCHEDULE_CRON = "0 7 * * *"

JOB_MAX_RETRIES = "1"
