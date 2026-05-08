"""On-disk embedding cache, keyed on (model, dims, sha256(text)).

Embeddings are deterministic for a given (model, dims, input) — caching them
between eval runs lets us iterate on chunker / dataset without re-paying
OpenAI for every text. Layout::

    cache_dir/
      <model>/
        d<dims>/
          <sha256>.json   # JSON-encoded list[float]

Cache is a write-through; misses fall back to the wrapped embedder and the
result is persisted before being returned.
"""

import hashlib
import json
from pathlib import Path

from .embedder import Embedder


class CachedEmbedder:
    def __init__(self, inner: Embedder, cache_dir: Path):
        self._inner = inner
        self._dir = cache_dir / inner.model / f"d{inner.dims}"
        self._dir.mkdir(parents=True, exist_ok=True)
        self.model = inner.model
        self.dims = inner.dims

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        results: list[list[float] | None] = [None] * len(texts)
        misses: list[tuple[int, str]] = []  # (output_index, text)

        for i, text in enumerate(texts):
            cached = self._read(text)
            if cached is not None:
                results[i] = cached
            else:
                misses.append((i, text))

        if misses:
            fresh = self._inner.embed_batch([t for _, t in misses])
            for (i, text), vec in zip(misses, fresh):
                results[i] = vec
                self._write(text, vec)

        # mypy: at this point all entries are populated.
        return [r for r in results if r is not None]

    def _key(self, text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def _path(self, text: str) -> Path:
        return self._dir / f"{self._key(text)}.json"

    def _read(self, text: str) -> list[float] | None:
        p = self._path(text)
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            # A partial write left a corrupt file; treat as miss + overwrite.
            return None

    def _write(self, text: str, vec: list[float]) -> None:
        # Write-then-rename for atomicity against concurrent eval runs.
        p = self._path(text)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(vec), encoding="utf-8")
        tmp.replace(p)
