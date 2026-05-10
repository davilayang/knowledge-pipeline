"""Eval-pair and result types for the retrieval eval harness.

JSONL line shape consumed by :mod:`evals.retrieval.dataset`::

    {"query": "...", "source": "raw_store", "expected_content_id": "abc123"}

``source`` is one of ``raw_store`` | ``notes`` | ``sessions`` | ``research`` and
must match a key in ``CHUNKER_BY_SOURCE``. ``expected_content_id`` is the
``content_id`` (= ``IngestItem.item_id``) the query is supposed to retrieve;
chunks are tagged with ``content_id`` in their metadata so retrieval can be
scored at the document level.
"""

from dataclasses import dataclass, field

VALID_SOURCES = ("raw_store", "notes", "sessions", "research")


@dataclass(frozen=True)
class EvalPair:
    query: str
    source: str
    expected_content_id: str


@dataclass(frozen=True)
class EvalConfig:
    embedding_model: str
    embedding_dims: int
    chunker_by_source: dict[str, str]
    chunk_size: int = 800
    chunk_overlap: int = 100
    recall_k: int = 5
    rank_k: int = 10
    item_limit: int | None = None


@dataclass
class SourceMetrics:
    source: str
    n_queries: int
    recall_at_5: float
    mrr_at_10: float
    ndcg_at_10: float


@dataclass
class EvalRunResult:
    """Aggregated output of one harness invocation."""

    embedding_model: str
    embedding_dims: int
    chunker_by_source: dict[str, str]
    per_source: list[SourceMetrics] = field(default_factory=list)
    started_at: str = ""
    finished_at: str = ""
