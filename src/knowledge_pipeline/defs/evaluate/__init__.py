import dagster as dg

from .assets import retrieval_quality_eval

eval_job = dg.define_asset_job(
    name="evaluate_retrievals",
    selection=[retrieval_quality_eval],
    description="Evaluate retrieval quality across RAG strategies",
)

defs = dg.Definitions(
    assets=[retrieval_quality_eval],
    jobs=[eval_job],
)
