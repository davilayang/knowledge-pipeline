"""In-memory Chroma stand-in for runner tests.

chromadb-client (the thin HTTP client) cannot run in embedded mode, so unit
tests synthesize the same surface the runner uses: get_or_create_collection,
upsert, query (cosine distance), get.
"""

import math


def _cosine_distance(a: list[float], b: list[float]) -> float:
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(x * x for x in b)) or 1.0
    dot = sum(x * y for x, y in zip(a, b))
    return 1.0 - dot / (na * nb)


class _FakeCollection:
    def __init__(self, name: str):
        self.name = name
        self._ids: list[str] = []
        self._docs: list[str] = []
        self._embs: list[list[float]] = []
        self._metas: list[dict] = []

    def upsert(self, *, ids, documents, embeddings, metadatas):
        for i, id_ in enumerate(ids):
            if id_ in self._ids:
                idx = self._ids.index(id_)
                self._docs[idx] = documents[i]
                self._embs[idx] = list(embeddings[i])
                self._metas[idx] = metadatas[i]
            else:
                self._ids.append(id_)
                self._docs.append(documents[i])
                self._embs.append(list(embeddings[i]))
                self._metas.append(metadatas[i])

    def query(self, *, query_embeddings, n_results, include):
        all_metas: list[list[dict]] = []
        for qe in query_embeddings:
            scored = sorted(
                range(len(self._ids)),
                key=lambda i, qe=qe: _cosine_distance(qe, self._embs[i]),
            )
            top = scored[:n_results]
            all_metas.append([self._metas[i] for i in top])
        return {"metadatas": all_metas}

    def get(self, *, include=None):
        return {"ids": list(self._ids), "metadatas": list(self._metas)}


class FakeChromaClient:
    def __init__(self):
        self._collections: dict[str, _FakeCollection] = {}

    def get_or_create_collection(self, *, name, embedding_function=None, metadata=None):
        if name not in self._collections:
            self._collections[name] = _FakeCollection(name)
        return self._collections[name]

    def get_collection(self, *, name):
        return self._collections[name]
