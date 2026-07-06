"""Behavioural tests for populate_vector_store assets.

Mocks at the import boundary: ``OpenAIEmbedder`` and Chroma collections are
stubbed at ``orchestrators.defs.populate_vector_store.assets``. No
live calls.
"""

from datetime import date
from unittest.mock import MagicMock, patch

import dagster as dg
import pytest
from domains.types import IngestItem
from orchestrators.defs.populate_vector_store import assets as pvs_assets
from orchestrators.defs.populate_vector_store.assets import (
    SOURCE_TO_COLLECTION,
    contents,
    conversations,
    pending,
    wiki,
)


class _StubCollection:
    """In-memory Chroma collection — tracks delete + upsert calls."""

    def __init__(
        self,
        existing_ids: list[str] | None = None,
        existing_metas: dict[str, dict] | None = None,
    ):
        self._ids: list[str] = list(existing_ids or [])
        self._docs: dict[str, str] = {}
        self._metas: dict[str, dict] = dict(existing_metas or {})
        self.delete_calls: list[dict] = []
        self.upsert_calls: list[dict] = []
        self.get_calls: list[dict] = []

    def get(self, where=None, include=None):
        self.get_calls.append({"where": where, "include": include})
        in_ids = (where or {}).get("content_id", {}).get("$in", [])
        matched = [cid for cid in self._ids if any(cid.startswith(f"{i}::chunk-") for i in in_ids)]
        result = {"ids": matched}
        if include and "metadatas" in include:
            result["metadatas"] = [self._metas.get(cid, {}) for cid in matched]
        return result

    def delete(self, where=None):
        self.delete_calls.append({"where": where})
        cid = (where or {}).get("content_id")
        self._ids = [i for i in self._ids if not i.startswith(f"{cid}::chunk-")]
        self._metas = {i: m for i, m in self._metas.items() if not i.startswith(f"{cid}::chunk-")}

    def upsert(self, ids, documents, embeddings, metadatas):
        self.upsert_calls.append(
            {
                "ids": list(ids),
                "documents": list(documents),
                "embeddings": list(embeddings),
                "metadatas": list(metadatas),
            }
        )
        for i, d, m in zip(ids, documents, metadatas):
            self._ids.append(i)
            self._docs[i] = d
            self._metas[i] = m


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
        wiki=None,
    ):
        self._raw_store = raw_store or _StubSource()
        self._notes = notes or _StubSource()
        self._sessions = sessions or _StubSource()
        self._wiki = wiki or _StubSource()

    def raw_store(self):
        return self._raw_store

    def notes(self):
        return self._notes

    def sessions(self):
        return self._sessions

    def wiki(self):
        return self._wiki


class _StubSource:
    def __init__(
        self,
        items: list[IngestItem] | None = None,
        resolve_index: dict[str, dict] | None = None,
    ):
        self._items = items or []
        self._by_id = {i.item_id: i for i in self._items}
        self._resolve_index = resolve_index or {}

    def get_item_ids(self) -> list[str]:
        return [i.item_id for i in self._items]

    def get_item(self, iid):
        return self._by_id.get(iid)

    def resolve_index(self) -> dict[str, dict]:
        return self._resolve_index


def _item(iid: str, text: str = "hello world", source_type: str = "raw_store") -> IngestItem:
    return IngestItem(
        item_id=iid,
        title=f"title-{iid}",
        date=date(2026, 5, 11),
        text=text,
        source_type=source_type,
        source_ref=f"{source_type}:{iid}",
    )


_MARKDOWN_BODY = """# Doc Title

## Section One

Body of section one.

## Section Two

Body of section two.
"""


def _markdown_item(iid: str, text: str = _MARKDOWN_BODY) -> IngestItem:
    """Item whose text is real markdown — exercises the production markdown
    chunker so `Chunk.heading` is populated with a breadcrumb."""
    return _item(iid, text=text, source_type="raw_store")


def _sessions_item(iid: str) -> IngestItem:
    """Item shaped like a session transcript (marker-delimited turns) so the
    turn_grouping chunker emits its time-range heading."""
    from domains.sessions.sources import TURN_MARKER_PREFIX

    body = (
        f"{TURN_MARKER_PREFIX} role=user ts=2026-04-01T14:00:00>>>\n"
        "hello there\n"
        f"{TURN_MARKER_PREFIX} role=assistant ts=2026-04-01T14:01:00>>>\n"
        "world reply"
    )
    return _item(iid, text=body, source_type="sessions")


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
    sources = _StubSources()  # all three sources empty by default

    ctx = MagicMock(spec=dg.AssetExecutionContext)
    result = pending.op.compute_fn.decorated_fn(ctx, sources=sources, vector_store=vector_store)

    assert result.value == {"raw_store": [], "notes": [], "sessions": [], "wiki": []}


def test_pending_relists_wiki_entity_with_changed_page_hash():
    """Wiki pages are rewritten daily but keep their entity_id, so a bare
    existence check would never re-embed a changed page (FM1b). An indexed entity
    whose live page_hash (resolve.json) differs from the stored one must re-list."""
    item = _wiki_item("e_a", num_sources=1)
    wiki_collection = _StubCollection(
        existing_ids=["e_a::chunk-0"],
        existing_metas={"e_a::chunk-0": {"page_hash": "old-hash"}},
    )
    vector_store = _StubVectorStore({"wiki": wiki_collection})
    resolve = {"e_a": {"page_hash": "new-hash", "snapshot_id": "snap-2"}}
    sources = _StubSources(wiki=_StubSource([item], resolve_index=resolve))

    ctx = MagicMock(spec=dg.AssetExecutionContext)
    with patch.object(pvs_assets, "MAX_PER_TICK_DEFAULT", 50):
        result = pending.op.compute_fn.decorated_fn(ctx, sources=sources, vector_store=vector_store)

    assert result.value["wiki"] == ["e_a"]


def test_pending_skips_wiki_entity_with_unchanged_page_hash():
    """An indexed wiki entity whose live page_hash matches the stored one is
    up-to-date — it must not be re-listed (no needless re-embed cost)."""
    item = _wiki_item("e_a", num_sources=1)
    wiki_collection = _StubCollection(
        existing_ids=["e_a::chunk-0"],
        existing_metas={"e_a::chunk-0": {"page_hash": "same-hash"}},
    )
    vector_store = _StubVectorStore({"wiki": wiki_collection})
    resolve = {"e_a": {"page_hash": "same-hash", "snapshot_id": "snap-1"}}
    sources = _StubSources(wiki=_StubSource([item], resolve_index=resolve))

    ctx = MagicMock(spec=dg.AssetExecutionContext)
    with patch.object(pvs_assets, "MAX_PER_TICK_DEFAULT", 50):
        result = pending.op.compute_fn.decorated_fn(ctx, sources=sources, vector_store=vector_store)

    assert result.value["wiki"] == []


# ------------------------------------------------------------------
# ingest asset
# ------------------------------------------------------------------


def _fake_embedder_class(dims: int = 1536):
    """Build a stub OpenAIEmbedder class. Instances expose ``embed_batch(texts)``
    returning ``dims``-vectors of the right count, and the class-level
    ``embed_batch`` MagicMock tracks calls across all instances."""

    embed_batch = MagicMock(side_effect=lambda texts: [[0.1] * dims for _ in texts])

    class _FakeEmbedder:
        def __init__(self, model: str, dims: int = dims):
            self.model = model
            self.dims = dims

        def embed_batch(self, texts):
            return embed_batch(texts)

    return _FakeEmbedder, embed_batch


def test_ingest_short_circuits_on_empty_pending():
    """Empty pending slice → no source.get_item, no chroma writes, no embedding."""
    sources = _StubSources(raw_store=_StubSource([_item("a")]))
    raw_collection = _StubCollection()
    vector_store = _StubVectorStore({"contents": raw_collection})
    ctx = MagicMock(spec=dg.AssetExecutionContext)

    fake_embedder, embed_batch = _fake_embedder_class()
    with patch.object(pvs_assets, "OpenAIEmbedder", fake_embedder):
        result = contents.op.compute_fn.decorated_fn(
            ctx, pending={"raw_store": []}, sources=sources, vector_store=vector_store
        )

    assert isinstance(result, dg.MaterializeResult)
    assert raw_collection.upsert_calls == []
    assert raw_collection.delete_calls == []
    embed_batch.assert_not_called()


def test_ingest_upserts_to_correct_collection():
    """contents asset writes to COLLECTION_CONTENTS with ids/documents/embeddings/metadatas."""
    sources = _StubSources(raw_store=_StubSource([_item("a", text="body")]))
    raw_collection = _StubCollection()
    vector_store = _StubVectorStore({"contents": raw_collection})
    ctx = MagicMock(spec=dg.AssetExecutionContext)
    fake_embedder, _ = _fake_embedder_class()

    with patch.object(pvs_assets, "OpenAIEmbedder", fake_embedder):
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
    fake_embedder, _ = _fake_embedder_class()

    with patch.object(pvs_assets, "OpenAIEmbedder", fake_embedder):
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
    fake_embedder, _ = _fake_embedder_class()

    with patch.object(pvs_assets, "OpenAIEmbedder", fake_embedder):
        contents.op.compute_fn.decorated_fn(
            ctx, pending={"raw_store": ["a"]}, sources=sources, vector_store=vector_store
        )

    assert raw_collection.delete_calls == [{"where": {"content_id": "a"}}]
    assert raw_collection.upsert_calls[0]["ids"] == ["a::chunk-0"]
    # Verify the old chunks 1 and 2 are gone post-delete; only the freshly-upserted chunk-0 remains.
    assert raw_collection._ids == ["a::chunk-0"]


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
    fake_embedder, _ = _fake_embedder_class()

    with (
        patch.object(pvs_assets, "OpenAIEmbedder", fake_embedder),
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
# wiki provenance metadata (FM2/FM4)
# ------------------------------------------------------------------


def _wiki_item(iid: str, *, num_sources: int, text: str = "An entity summary.") -> IngestItem:
    return IngestItem(
        item_id=iid,
        title=f"title-{iid}",
        date=date(2026, 6, 20),
        text=text,
        source_type="wiki",
        source_ref=f"wiki:{iid}",
        num_sources=num_sources,
    )


def test_wiki_ingest_stamps_provenance_metadata():
    """A wiki vector carries num_sources (FM4 single-source hedge) plus the
    page_hash + snapshot_id (FM2 staleness) that NA needs to detect a stale hit.
    All three come from resolve.json via the resolve_index side-input — the
    single provenance authority — not the page frontmatter."""
    item = _wiki_item("e_a", num_sources=3)
    resolve = {"e_a": {"page_hash": "hash-a", "snapshot_id": "snap-1", "num_sources": 3}}
    sources = _StubSources(wiki=_StubSource([item], resolve_index=resolve))
    wiki_collection = _StubCollection()
    vector_store = _StubVectorStore({"wiki": wiki_collection})
    ctx = MagicMock(spec=dg.AssetExecutionContext)
    fake_embedder, _ = _fake_embedder_class()

    with patch.object(pvs_assets, "OpenAIEmbedder", fake_embedder):
        wiki.op.compute_fn.decorated_fn(
            ctx, pending={"wiki": ["e_a"]}, sources=sources, vector_store=vector_store
        )

    md = wiki_collection.upsert_calls[0]["metadatas"][0]
    assert md["content_id"] == "e_a"
    assert md["num_sources"] == 3
    assert md["page_hash"] == "hash-a"
    assert md["snapshot_id"] == "snap-1"


def test_wiki_ingest_fails_fast_when_provenance_missing():
    """A pending wiki entity absent from resolve.json (or missing a provenance
    field) means resolve.json is behind the pages on disk — a stale index or a
    torn mid-write read. Embedding it would ship a vector without the required
    FM2/FM4 provenance, so the asset fails fast and embeds nothing."""
    item = _wiki_item("e_a", num_sources=3)
    sources = _StubSources(wiki=_StubSource([item], resolve_index={}))  # no provenance
    wiki_collection = _StubCollection()
    vector_store = _StubVectorStore({"wiki": wiki_collection})
    ctx = MagicMock(spec=dg.AssetExecutionContext)
    fake_embedder, embed_batch = _fake_embedder_class()

    with (
        patch.object(pvs_assets, "OpenAIEmbedder", fake_embedder),
        pytest.raises(dg.Failure),
    ):
        wiki.op.compute_fn.decorated_fn(
            ctx, pending={"wiki": ["e_a"]}, sources=sources, vector_store=vector_store
        )

    embed_batch.assert_not_called()
    assert wiki_collection.upsert_calls == []


def test_non_wiki_source_omits_provenance_metadata():
    """num_sources / page_hash / snapshot_id are wiki-only — a raw_store vector
    must not carry them (num_sources is None on the item; no resolve side-input)."""
    sources = _StubSources(raw_store=_StubSource([_item("a", text="body")]))
    raw_collection = _StubCollection()
    vector_store = _StubVectorStore({"contents": raw_collection})
    ctx = MagicMock(spec=dg.AssetExecutionContext)
    fake_embedder, _ = _fake_embedder_class()

    with patch.object(pvs_assets, "OpenAIEmbedder", fake_embedder):
        contents.op.compute_fn.decorated_fn(
            ctx, pending={"raw_store": ["a"]}, sources=sources, vector_store=vector_store
        )

    md = raw_collection.upsert_calls[0]["metadatas"][0]
    assert "num_sources" not in md
    assert "page_hash" not in md
    assert "snapshot_id" not in md


# ------------------------------------------------------------------
# wiring sanity
# ------------------------------------------------------------------


def test_source_to_collection_covers_all_sources():
    assert {n for n, _ in SOURCE_TO_COLLECTION} == {"raw_store", "notes", "sessions", "wiki"}


# ------------------------------------------------------------------
# heading wiring (Chunk.heading → Chroma metadata + embed-text prefix)
# ------------------------------------------------------------------


def test_ingest_writes_heading_path_metadata():
    """Markdown-chunked item lands `heading_path` in Chroma metadata per chunk."""
    sources = _StubSources(raw_store=_StubSource([_markdown_item("a")]))
    raw_collection = _StubCollection()
    vector_store = _StubVectorStore({"contents": raw_collection})
    ctx = MagicMock(spec=dg.AssetExecutionContext)
    fake_embedder, _ = _fake_embedder_class()

    with patch.object(pvs_assets, "OpenAIEmbedder", fake_embedder):
        contents.op.compute_fn.decorated_fn(
            ctx, pending={"raw_store": ["a"]}, sources=sources, vector_store=vector_store
        )

    metadatas = raw_collection.upsert_calls[0]["metadatas"]
    heading_paths = [m.get("heading_path") for m in metadatas]
    assert "Doc Title > Section One" in heading_paths
    assert "Doc Title > Section Two" in heading_paths


def test_ingest_embeds_heading_prefix_for_markdown_source():
    """Markdown source: embed_batch is called with heading-prefixed text;
    the stored `documents` field stays clean (no prefix)."""
    sources = _StubSources(raw_store=_StubSource([_markdown_item("a")]))
    raw_collection = _StubCollection()
    vector_store = _StubVectorStore({"contents": raw_collection})
    ctx = MagicMock(spec=dg.AssetExecutionContext)
    fake_embedder, embed_batch = _fake_embedder_class()

    with patch.object(pvs_assets, "OpenAIEmbedder", fake_embedder):
        contents.op.compute_fn.decorated_fn(
            ctx, pending={"raw_store": ["a"]}, sources=sources, vector_store=vector_store
        )

    # Embedded text: heading prefix present.
    embed_args = embed_batch.call_args.args[0]
    assert all(t.startswith("Doc Title > Section ") for t in embed_args)
    assert all("\n\n" in t for t in embed_args)

    # Stored documents: prefix absent.
    stored = raw_collection.upsert_calls[0]["documents"]
    for doc in stored:
        assert not doc.startswith("Doc Title > Section ")
        # Sanity — the chunk body content is still there.
        assert "Body of section" in doc


def test_ingest_does_not_embed_heading_prefix_for_turn_grouping():
    """Sessions source uses turn_grouping whose heading is a time-range, not
    semantic — it must not be prepended to embedded text."""
    sources = _StubSources(sessions=_StubSource([_sessions_item("s1")]))
    sess_collection = _StubCollection()
    vector_store = _StubVectorStore({"conversations": sess_collection})
    ctx = MagicMock(spec=dg.AssetExecutionContext)
    fake_embedder, embed_batch = _fake_embedder_class()

    with patch.object(pvs_assets, "OpenAIEmbedder", fake_embedder):
        conversations.op.compute_fn.decorated_fn(
            ctx, pending={"sessions": ["s1"]}, sources=sources, vector_store=vector_store
        )

    embed_args = embed_batch.call_args.args[0]
    # The time-range heading must NOT have been prepended to any embedded text.
    for t in embed_args:
        assert not t.startswith("turns ")

    # Heading metadata still lands on the stored chunks (for filterability).
    metadatas = sess_collection.upsert_calls[0]["metadatas"]
    assert any(m.get("heading_path", "").startswith("turns ") for m in metadatas)
