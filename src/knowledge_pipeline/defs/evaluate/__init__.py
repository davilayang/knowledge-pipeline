import dagster as dg

from .ops import eval_graph

eval_job = eval_graph.to_job(
    name="evaluate_retrieval_strategies",
    description="Evaluate retrieval quality across RAG strategies",
)

defs = dg.Definitions(
    jobs=[eval_job],
)
