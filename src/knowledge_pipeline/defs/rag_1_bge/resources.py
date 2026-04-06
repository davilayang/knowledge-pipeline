# Dagster resources for the BGE RAG strategy.
# Only difference from baseline: embedding_model and collection_name.

from knowledge_pipeline.defs.rag_0_baseline.resources import VectorStoreResource as _Base


class VectorStoreResource(_Base):
    collection_name: str = "bge"
    embedding_model: str | None = "BAAI/bge-small-en-v1.5"
