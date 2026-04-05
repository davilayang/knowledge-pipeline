import dagster as dg

from .assets import eval_comparison, eval_strategy_run

eval_strategy_job = dg.define_asset_job(
    name="evaluate_strategy",
    selection=[eval_strategy_run],
    description="Evaluate retrieval quality for a specific (collection, strategy) combo",
    partitions_def=eval_strategy_run.partitions_def,
)

eval_comparison_job = dg.define_asset_job(
    name="evaluate_comparison",
    selection=[eval_comparison],
    description="Compare all evaluated strategy combos",
)

defs = dg.Definitions(
    assets=[eval_strategy_run, eval_comparison],
    jobs=[eval_strategy_job, eval_comparison_job],
)
