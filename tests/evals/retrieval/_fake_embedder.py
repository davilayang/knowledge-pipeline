"""Hash-based deterministic embedder used only by the retrieval-eval test suite."""

import hashlib


class DeterministicFakeEmbedder:
    def __init__(self, model: str = "fake", dims: int = 16):
        self.model = model
        self.dims = dims

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for text in texts:
            h = hashlib.sha256(text.encode("utf-8")).digest()
            vec = [(h[i % len(h)] / 255.0) for i in range(self.dims)]
            norm = sum(x * x for x in vec) ** 0.5 or 1.0
            out.append([x / norm for x in vec])
        return out
