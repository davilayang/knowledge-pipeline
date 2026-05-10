"""End-to-end runner tests against an in-memory chroma + fake embedder.

We exercise the real Chroma client (EphemeralClient) and real chunking
registry — only the embedder is faked, so the indexing / query path matches
production behaviour byte-for-byte.
"""

from datetime import date

import chromadb
import pytest
from domains.types import IngestItem
from evals.retrieval.embedder import DeterministicFakeEmbedder
from evals.retrieval.runner import run_eval
from evals.retrieval.types import EvalConfig, EvalPair


def _item(item_id: str, text: str, source_type: str = "raw_store") -> IngestItem:
    return IngestItem(
        item_id=item_id,
        title=f"item {item_id}",
        date=date(2026, 4, 1),
        text=text,
        source_type=source_type,
        source_ref=f"{source_type}:{item_id}",
    )


@pytest.fixture
def chroma():
    return chromadb.EphemeralClient()


class TestRunEval:
    def test_perfect_recall_when_query_matches_doc(self, chroma):
        items = [
            _item("doc_a", "alpha bravo charlie"),
            _item("doc_b", "delta echo foxtrot"),
            _item("doc_c", "golf hotel india"),
        ]
        # Query strings are identical to the doc body so the deterministic
        # fake embedder produces matching vectors.
        pairs = [
            EvalPair("alpha bravo charlie", "raw_store", "doc_a"),
            EvalPair("delta echo foxtrot", "raw_store", "doc_b"),
        ]
        config = EvalConfig(
            embedding_model="fake",
            embedding_dims=16,
            chunker_by_source={"raw_store": "fixed"},
            chunk_size=400,
            chunk_overlap=0,
        )
        result = run_eval(
            config=config,
            eval_pairs=pairs,
            items_by_source={"raw_store": items},
            embedder=DeterministicFakeEmbedder(dims=16),
            chroma_client=chroma,
            collection_prefix="test_perfect",
        )
        per = {m.source: m for m in result.per_source}
        assert per["raw_store"].n_queries == 2
        assert per["raw_store"].recall_at_5 == 1.0
        assert per["raw_store"].mrr_at_10 == 1.0

    def test_zero_recall_when_query_mismatches(self, chroma):
        items = [_item("doc_a", "alpha bravo charlie")]
        pairs = [EvalPair("totally unrelated text", "raw_store", "missing_doc")]
        config = EvalConfig(
            embedding_model="fake",
            embedding_dims=16,
            chunker_by_source={"raw_store": "fixed"},
            chunk_size=400,
            chunk_overlap=0,
        )
        result = run_eval(
            config=config,
            eval_pairs=pairs,
            items_by_source={"raw_store": items},
            embedder=DeterministicFakeEmbedder(dims=16),
            chroma_client=chroma,
            collection_prefix="test_zero",
        )
        per = {m.source: m for m in result.per_source}
        assert per["raw_store"].recall_at_5 == 0.0
        assert per["raw_store"].mrr_at_10 == 0.0

    def test_skips_sources_without_items(self, chroma):
        items = [_item("doc_a", "alpha")]
        pairs = [
            EvalPair("alpha", "raw_store", "doc_a"),
            EvalPair("anything", "notes", "n1"),  # no items for notes
        ]
        config = EvalConfig(
            embedding_model="fake",
            embedding_dims=16,
            chunker_by_source={"raw_store": "fixed", "notes": "fixed"},
        )
        result = run_eval(
            config=config,
            eval_pairs=pairs,
            items_by_source={"raw_store": items},  # notes missing
            embedder=DeterministicFakeEmbedder(dims=16),
            chroma_client=chroma,
            collection_prefix="test_skip",
        )
        sources = {m.source for m in result.per_source}
        assert "raw_store" in sources
        assert "notes" not in sources

    def test_item_limit_caps_indexing(self, chroma):
        items = [_item(f"doc_{i}", f"text_{i}") for i in range(20)]
        pairs = [EvalPair("text_5", "raw_store", "doc_5")]
        config = EvalConfig(
            embedding_model="fake",
            embedding_dims=16,
            chunker_by_source={"raw_store": "fixed"},
            item_limit=3,
        )
        result = run_eval(
            config=config,
            eval_pairs=pairs,
            items_by_source={"raw_store": items},
            embedder=DeterministicFakeEmbedder(dims=16),
            chroma_client=chroma,
            collection_prefix="test_limit",
        )
        # doc_5 wasn't indexed → recall@5 = 0
        assert result.per_source[0].recall_at_5 == 0.0

    def test_chunks_carry_content_id_metadata(self, chroma):
        # The runner must tag every chunk with content_id, _embedding_model,
        # and _embedding_dims so downstream metrics + drift detection work.
        items = [_item("doc_a", "alpha bravo charlie")]
        config = EvalConfig(
            embedding_model="fake",
            embedding_dims=16,
            chunker_by_source={"raw_store": "fixed"},
        )
        run_eval(
            config=config,
            eval_pairs=[],
            items_by_source={"raw_store": items},
            embedder=DeterministicFakeEmbedder(dims=16),
            chroma_client=chroma,
            collection_prefix="test_meta",
        )
        coll = chroma.get_collection(name="test_meta_raw_store")
        rows = coll.get(include=["metadatas"])
        assert rows["metadatas"]
        sample = rows["metadatas"][0]
        assert sample["content_id"] == "doc_a"
        assert sample["_embedding_model"] == "fake"
        assert sample["_embedding_dims"] == 16
