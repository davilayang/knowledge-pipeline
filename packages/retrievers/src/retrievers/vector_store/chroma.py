import chromadb

COLLECTION_NAME = "contents"


def get_http_client(host: str, port: int) -> chromadb.ClientAPI:
    """Connect to a remote ChromaDB server over HTTP."""
    return chromadb.HttpClient(host=host, port=port)


def get_collection(client: chromadb.ClientAPI, name: str) -> chromadb.Collection:
    """Get or create a ChromaDB collection. Embeddings are supplied by the caller."""
    return client.get_or_create_collection(
        name=name,
        embedding_function=None,  # type: ignore[arg-type]
        metadata={"hnsw:space": "cosine"},
    )
