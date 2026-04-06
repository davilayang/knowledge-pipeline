# Dagster resources for the markdown + BGE index strategy.
# Only difference from markdown_minilm: embedding_model and collection_name.

from knowledge_pipeline.defs.idx_markdown_minilm.resources import VectorStoreResource as _Base


class VectorStoreResource(_Base):
    collection_name: str = "markdown_bge"
    embedding_model: str | None = "BAAI/bge-small-en-v1.5"
