"""Behavioural tests for populate_vector_store assets.

Mocks at the import boundary: ``OpenAI`` and Chroma collections are stubbed
at ``orchestrators.defs.pipelines.populate_vector_store.assets``. No live calls.
"""

from datetime import date
from unittest.mock import MagicMock, patch

import dagster as dg
import pytest
from domains.types import IngestItem
from openai import APIConnectionError
from orchestrators.defs.pipelines.populate_vector_store import assets as pvs_assets
from orchestrators.defs.pipelines.populate_vector_store.assets import (
    SOURCE_TO_COLLECTION,
    contents,
    pending,
)


class _StubCollection:
    """In-memory Chroma collection — tracks delete + upsert calls."""

    def __init__(self, existing_ids: list[str] | None = None):
        self._ids: list[str] = list(existing_ids or [])
        self._docs: dict[str, str] = {}
        self.delete_calls: list[dict] = []
        self.upsert_calls: list[dict] = []
        self.get_calls: list[dict] = []

    def get(self, where=None, include=None):
        self.get_calls.append({"where": where})
        in_ids = (where or {}).get("content_id", {}).get("$in", [])
        matched = [cid for cid in self._ids if any(cid.startswith(f"{i}::chunk-") for i in in_ids)]
        return {"ids": matched}

    def delete(self, where=None):
        self.delete_calls.append({"where": where})
        cid = (where or {}).get("content_id")
        self._ids = [i for i in self._ids if not i.startswith(f"{cid}::chunk-")]

    def upsert(self, ids, documents, embeddings, metadatas):
        self.upsert_calls.append(
            {
                "ids": list(ids),
                "documents": list(documents),
                "embeddings": list(embeddings),
                "metadatas": list(metadatas),
            }
        )
        for i, d in zip(ids, documents):
            self._ids.append(i)
            self._docs[i] = d


class _StubVectorStore:
    def __init__(self, collections: dict[str, _StubCollection]):
        self._collections = collections

    def get_collection(self, name):
        return self._collections.setdefault(name, _StubCollection())


class _StubSources:
    """Mimics SourcesResource — each method returns a configured stub source."""

    def __init__(
        self,
        raw_store=None,
        notes=None,
        sessions=None,
        research=None,
    ):
        self._raw_store = raw_store or _StubSource()
        self._notes = notes or _StubSource()
        self._sessions = sessions or _StubSource()
        self._research = research or _StubSource()

    def raw_store(self):
        return self._raw_store

    def notes(self):
        return self._notes

    def sessions(self):
        return self._sessions

    def research(self):
        return self._research


class _StubSource:
    def __init__(self, items: list[IngestItem] | None = None):
        self._items = items or []
        self._by_id = {i.item_id: i for i in self._items}

    def get_item_ids(self) -> list[str]:
        return [i.item_id for i in self._items]

    def get_item(self, iid):
        return self._by_id.get(iid)


def _item(iid: str, text: str = "hello world", source_type: str = "raw_store") -> IngestItem:
    return IngestItem(
        item_id=iid,
        title=f"title-{iid}",
        date=date(2026, 5, 11),
        text=text,
        source_type=source_type,
        source_ref=f"{source_type}:{iid}",
    )


# ------------------------------------------------------------------
# pending discovery
# ------------------------------------------------------------------


def test_pending_returns_only_unindexed_per_source():
    """Already-indexed ids are filtered out; the rest are capped per-source."""
    raw_items = [_item(f"raw-{i}") for i in range(5)]
    raw_collection = _StubCollection(
        existing_ids=["raw-0::chunk-0", "raw-0::chunk-1", "raw-2::chunk-0"]
    )
    vector_store = _StubVectorStore({"contents": raw_collection})
    sources = _StubSources(raw_store=_StubSource(raw_items))

    ctx = MagicMock(spec=dg.AssetExecutionContext)
    with patch.object(pvs_assets, "MAX_PER_TICK_DEFAULT", 50):
        result = pending.op.compute_fn.decorated_fn(ctx, sources=sources, vector_store=vector_store)

    assert result.value["raw_store"] == ["raw-1", "raw-3", "raw-4"]
    # Other sources are empty (their stubs returned no items).
    assert result.value["notes"] == []
    assert result.value["sessions"] == []
    assert result.value["research"] == []


def test_pending_in_batching_caps_at_500_ids():
    """1200 ids across the raw_store source → ceil(1200/500) = 3 chroma .get() calls."""
    raw_items = [_item(f"raw-{i}") for i in range(1200)]
    raw_collection = _StubCollection()
    vector_store = _StubVectorStore({"contents": raw_collection})
    sources = _StubSources(raw_store=_StubSource(raw_items))

    ctx = MagicMock(spec=dg.AssetExecutionContext)
    with patch.object(pvs_assets, "MAX_PER_TICK_DEFAULT", 1500):
        pending.op.compute_fn.decorated_fn(ctx, sources=sources, vector_store=vector_store)

    assert len(raw_collection.get_calls) == 3


def test_pending_zero_items_in_source_short_circuits():
    """A source that returns no ids yields an empty pending list without crashing."""
    vector_store = _StubVectorStore({})
    sources = _StubSources()  # all four sources empty by default

    ctx = MagicMock(spec=dg.AssetExecutionContext)
    result = pending.op.compute_fn.decorated_fn(ctx, sources=sources, vector_store=vector_store)

    assert result.value == {"raw_store": [], "notes": [], "sessions": [], "research": []}


# ------------------------------------------------------------------
# ingest asset
# ------------------------------------------------------------------


def _fake_openai_class(dims: int = 1536):
    """Build a MagicMock that mimics ``OpenAI()``: returns a client whose
    ``embeddings.create(...)`` produces ``dims``-vectors of the right count."""

    class _Resp:
        def __init__(self, n):
            self.data = [MagicMock(embedding=[0.1] * dims) for _ in range(n)]

    client = MagicMock()
    client.embeddings.create.side_effect = lambda model, input, dimensions: _Resp(len(input))
    klass = MagicMock(return_value=client)
    return klass, client


def test_ingest_short_circuits_on_empty_pending():
    """Empty pending slice → no source.get_item, no chroma writes, no OpenAI."""
    sources = _StubSources(raw_store=_StubSource([_item("a")]))
    raw_collection = _StubCollection()
    vector_store = _StubVectorStore({"contents": raw_collection})
    ctx = MagicMock(spec=dg.AssetExecutionContext)

    fake_openai, fake_client = _fake_openai_class()
    with patch.object(pvs_assets, "OpenAI", fake_openai):
        result = contents.op.compute_fn.decorated_fn(
            ctx, pending={"raw_store": []}, sources=sources, vector_store=vector_store
        )

    assert isinstance(result, dg.MaterializeResult)
    assert raw_collection.upsert_calls == []
    assert raw_collection.delete_calls == []
    fake_client.embeddings.create.assert_not_called()


def test_ingest_upserts_to_correct_collection():
    """contents asset writes to COLLECTION_CONTENTS with ids/documents/embeddings/metadatas."""
    sources = _StubSources(raw_store=_StubSource([_item("a", text="body")]))
    raw_collection = _StubCollection()
    vector_store = _StubVectorStore({"contents": raw_collection})
    ctx = MagicMock(spec=dg.AssetExecutionContext)
    fake_openai, _ = _fake_openai_class()

    with patch.object(pvs_assets, "OpenAI", fake_openai):
        contents.op.compute_fn.decorated_fn(
            ctx, pending={"raw_store": ["a"]}, sources=sources, vector_store=vector_store
        )

    assert len(raw_collection.upsert_calls) == 1
    call = raw_collection.upsert_calls[0]
    assert call["ids"] == ["a::chunk-0"]
    assert call["documents"] == ["body"]
    assert len(call["embeddings"]) == 1
    assert call["metadatas"][0]["content_id"] == "a"
    assert call["metadatas"][0]["chunk_index"] == 0
    assert call["metadatas"][0]["_embedding_model"] == "text-embedding-3-small"
    assert call["metadatas"][0]["_embedding_dims"] == 1536


def test_ingest_idempotent_rerun_yields_same_ids():
    """Deterministic chunk ids — re-running with the same source state produces
    identical (id, doc) pairs in the second upsert."""
    sources = _StubSources(raw_store=_StubSource([_item("a", text="x")]))
    raw_collection = _StubCollection()
    vector_store = _StubVectorStore({"contents": raw_collection})
    ctx = MagicMock(spec=dg.AssetExecutionContext)
    fake_openai, _ = _fake_openai_class()

    with patch.object(pvs_assets, "OpenAI", fake_openai):
        contents.op.compute_fn.decorated_fn(
            ctx, pending={"raw_store": ["a"]}, sources=sources, vector_store=vector_store
        )
        contents.op.compute_fn.decorated_fn(
            ctx, pending={"raw_store": ["a"]}, sources=sources, vector_store=vector_store
        )

    assert raw_collection.upsert_calls[0]["ids"] == raw_collection.upsert_calls[1]["ids"]
    # delete called both times → no orphan chunks left from earlier runs.
    assert len(raw_collection.delete_calls) == 2


def test_ingest_body_shrink_deletes_stale_chunks():
    """3 chunks shrinks to 1 chunk → the prior 3 chunks must be removed before
    the new 1-chunk upsert lands. Implementation invariant: delete-by-content_id
    runs before upsert."""
    long_then_short = [_item("a", text="x")]
    sources = _StubSources(raw_store=_StubSource(long_then_short))

    raw_collection = _StubCollection(existing_ids=["a::chunk-0", "a::chunk-1", "a::chunk-2"])
    vector_store = _StubVectorStore({"contents": raw_collection})
    ctx = MagicMock(spec=dg.AssetExecutionContext)
    fake_openai, _ = _fake_openai_class()

    with patch.object(pvs_assets, "OpenAI", fake_openai):
        contents.op.compute_fn.decorated_fn(
            ctx, pending={"raw_store": ["a"]}, sources=sources, vector_store=vector_store
        )

    assert raw_collection.delete_calls == [{"where": {"content_id": "a"}}]
    assert raw_collection.upsert_calls[0]["ids"] == ["a::chunk-0"]
    # Verify the old chunks 1 and 2 are gone post-delete; only the freshly-upserted chunk-0 remains.
    assert raw_collection._ids == ["a::chunk-0"]


def test_ingest_retries_transient_openai_error(monkeypatch):
    """Tenacity passes through a transient APIConnectionError: the embeddings
    call raises once, then succeeds."""
    sources = _StubSources(raw_store=_StubSource([_item("a", text="hello")]))
    raw_collection = _StubCollection()
    vector_store = _StubVectorStore({"contents": raw_collection})
    ctx = MagicMock(spec=dg.AssetExecutionContext)

    client = MagicMock()
    calls = {"n": 0}

    class _Resp:
        def __init__(self, n):
            self.data = [MagicMock(embedding=[0.1] * 1536) for _ in range(n)]

    def _create(model, input, dimensions):
        calls["n"] += 1
        if calls["n"] == 1:
            raise APIConnectionError(request=MagicMock())
        return _Resp(len(input))

    client.embeddings.create.side_effect = _create
    fake_openai = MagicMock(return_value=client)

    # Make tenacity backoff instant for the test.
    import tenacity

    monkeypatch.setattr(tenacity.nap.time, "sleep", lambda _s: None)

    with patch.object(pvs_assets, "OpenAI", fake_openai):
        contents.op.compute_fn.decorated_fn(
            ctx, pending={"raw_store": ["a"]}, sources=sources, vector_store=vector_store
        )

    assert calls["n"] == 2
    assert len(raw_collection.upsert_calls) == 1


def test_ingest_raises_on_per_item_failure():
    """If a per-item future raises, the asset raises dg.Failure (so retries
    pick it up). Successful items in the same tick stay committed."""
    sources = _StubSources(
        raw_store=_StubSource([_item("good", text="ok"), _item("bad", text="ok")])
    )

    class _PoisonCollection(_StubCollection):
        def upsert(self, ids, documents, embeddings, metadatas):
            if any(i.startswith("bad::") for i in ids):
                raise RuntimeError("chroma write blew up")
            super().upsert(ids, documents, embeddings, metadatas)

    poison = _PoisonCollection()
    vector_store = _StubVectorStore({"contents": poison})
    ctx = MagicMock(spec=dg.AssetExecutionContext)
    fake_openai, _ = _fake_openai_class()

    # Force serial execution so we can reason about which call lands first.
    with (
        patch.object(pvs_assets, "OpenAI", fake_openai),
        patch.object(pvs_assets, "INGEST_CONCURRENCY", 1),
        pytest.raises(dg.Failure),
    ):
        contents.op.compute_fn.decorated_fn(
            ctx,
            pending={"raw_store": ["good", "bad"]},
            sources=sources,
            vector_store=vector_store,
        )

    # The "good" item lands; "bad" did not.
    assert any("good::chunk-0" in c["ids"] for c in poison.upsert_calls)
    assert not any("bad::chunk-0" in c["ids"] for c in poison.upsert_calls)


# ------------------------------------------------------------------
# wiring sanity
# ------------------------------------------------------------------


def test_source_to_collection_covers_all_four_sources():
    assert {n for n, _ in SOURCE_TO_COLLECTION} == {"raw_store", "notes", "sessions", "research"}
