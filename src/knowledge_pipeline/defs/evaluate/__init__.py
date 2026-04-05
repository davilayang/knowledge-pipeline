import dagster as dg

from .assets import snapshot_eval

eval_job = dg.define_asset_job(
    name="evaluate_retrievals",
    selection=[snapshot_eval],
    description="Evaluate retrieval quality across RAG strategies",
)

defs = dg.Definitions(
    assets=[snapshot_eval],
    jobs=[eval_job],
)
