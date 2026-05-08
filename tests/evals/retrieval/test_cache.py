from pathlib import Path

from evals.retrieval.cache import CachedEmbedder
from evals.retrieval.embedder import DeterministicFakeEmbedder


class _CountingEmbedder:
    """Wraps a real embedder and counts how many texts pass through."""

    def __init__(self, inner):
        self._inner = inner
        self.model = inner.model
        self.dims = inner.dims
        self.calls = 0

    def embed_batch(self, texts):
        self.calls += len(texts)
        return self._inner.embed_batch(texts)


class TestCachedEmbedder:
    def test_first_call_misses(self, tmp_path: Path):
        counting = _CountingEmbedder(DeterministicFakeEmbedder(dims=8))
        cached = CachedEmbedder(counting, cache_dir=tmp_path)
        cached.embed_batch(["one", "two"])
        assert counting.calls == 2

    def test_repeat_call_is_full_cache_hit(self, tmp_path: Path):
        counting = _CountingEmbedder(DeterministicFakeEmbedder(dims=8))
        cached = CachedEmbedder(counting, cache_dir=tmp_path)
        cached.embed_batch(["one", "two"])
        cached.embed_batch(["one", "two"])
        # Second call should hit the cache for both texts.
        assert counting.calls == 2

    def test_partial_cache_hit_only_embeds_misses(self, tmp_path: Path):
        counting = _CountingEmbedder(DeterministicFakeEmbedder(dims=8))
        cached = CachedEmbedder(counting, cache_dir=tmp_path)
        cached.embed_batch(["one"])
        counting.calls = 0
        cached.embed_batch(["one", "two", "three"])
        assert counting.calls == 2  # only "two" and "three" embedded

    def test_returns_correct_vectors(self, tmp_path: Path):
        inner = DeterministicFakeEmbedder(dims=8)
        cached = CachedEmbedder(inner, cache_dir=tmp_path)
        # Same input twice — vectors must match (cache hit yields same value).
        v1 = cached.embed_batch(["text"])[0]
        v2 = cached.embed_batch(["text"])[0]
        assert v1 == v2
        # Different input — different vector.
        v3 = cached.embed_batch(["other"])[0]
        assert v1 != v3

    def test_persists_across_instances(self, tmp_path: Path):
        first = CachedEmbedder(DeterministicFakeEmbedder(dims=8), cache_dir=tmp_path)
        first.embed_batch(["persist-me"])

        counting = _CountingEmbedder(DeterministicFakeEmbedder(dims=8))
        second = CachedEmbedder(counting, cache_dir=tmp_path)
        second.embed_batch(["persist-me"])
        assert counting.calls == 0  # served from disk

    def test_recovers_from_corrupt_cache_file(self, tmp_path: Path):
        counting = _CountingEmbedder(DeterministicFakeEmbedder(dims=8))
        cached = CachedEmbedder(counting, cache_dir=tmp_path)
        cached.embed_batch(["x"])
        # Corrupt the cached file.
        cache_root = tmp_path / "fake" / "d8"
        (next(iter(cache_root.glob("*.json")))).write_text("not-json")

        counting.calls = 0
        cached.embed_batch(["x"])
        assert counting.calls == 1
