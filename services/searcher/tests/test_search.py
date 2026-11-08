"""POST /v1/search against the search-results contract (hub `contracts/search.md`)."""

import asyncio

import pytest
from fastapi.testclient import TestClient

from searcher.app import create_app
from searcher.models import SearchRequest


def _search(body: dict):
    with TestClient(create_app()) as client:
        return client.post("/v1/search", json=body)


def test_a_valid_request_is_answered_unavailable_with_every_lane_skipped() -> None:
    """Until lanes are wired, no lane runs, so status precedence rule 1 applies:
    no lane finished `ok`, so `unavailable` and no items."""
    resp = _search(
        {"contract_version": 2, "query": "rag", "caller": "mcp", "sources": ["corpus", "wiki"]}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["contract_version"] == 2
    assert body["status"] == "unavailable"
    assert body["items"] == []
    assert body["lanes"] == {
        "corpus": {"outcome": "skipped", "candidates": 0, "returned": 0},
        "wiki": {"outcome": "skipped", "candidates": 0, "returned": 0},
    }


def test_an_unknown_request_field_is_rejected() -> None:
    """A misspelt filter must never widen a search (contract §1)."""
    resp = _search({"contract_version": 2, "query": "rag", "caller": "mcp", "sorces": ["wiki"]})
    assert resp.status_code == 422


@pytest.mark.parametrize("version", [1, 3])
def test_a_contract_version_this_service_does_not_serve_is_refused(version: int) -> None:
    """Only version 2 is served: version 1 had no consumer (contract §3)."""
    resp = _search({"contract_version": version, "query": "rag", "caller": "mcp"})
    assert resp.status_code == 422


def test_every_response_field_has_a_contract_entry_in_arrival_order() -> None:
    """Mirrors `contracts/search.md` §2. A field added to the models without a
    §2 entry (or a §2 entry with no field) fails here; change the contract first."""
    from searcher.models import Item, Lane, MatchedBy, SearchResponse

    assert list(SearchResponse.model_fields) == [
        "contract_version",
        "status",
        "lanes",
        "items",
        "trace",
    ]
    assert list(Lane.model_fields) == ["outcome", "candidates", "returned"]
    assert list(Item.model_fields) == [
        "uri",
        "kind",
        "title",
        "author",
        "date",
        "url",
        "summary",
        "passages",
        "meta",
        "matched_by",
    ]
    assert list(MatchedBy.model_fields) == ["query", "lane", "method", "rank", "score"]


def test_healthz_answers_ok() -> None:
    with TestClient(create_app()) as client:
        resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


@pytest.mark.parametrize(
    "field, value",
    [
        pytest.param("contract_version", True, id="version_bool"),
        pytest.param("k", True, id="k_bool"),
        pytest.param("k", "5", id="k_string"),
        pytest.param("k", None, id="k_null"),
        pytest.param("sources", None, id="sources_null"),
        pytest.param("sources", [], id="sources_empty"),
        pytest.param("since", 0, id="since_number"),
        pytest.param("k_per_source", None, id="k_per_source_null"),
        pytest.param("k_per_source", {"corpus": 0}, id="k_per_source_zero"),
        pytest.param("k_per_source", {"corpus": 51}, id="k_per_source_over_50"),
        pytest.param("k_per_source", {"podcasts": 3}, id="k_per_source_unknown_lane"),
        pytest.param("deadline_ms", None, id="deadline_null"),
        pytest.param("deadline_ms", 0, id="deadline_zero"),
        pytest.param("deadline_ms", 60001, id="deadline_over_60s"),
        pytest.param("deadline_ms", True, id="deadline_bool"),
        pytest.param("since", "2026-11-04T00:00:00", id="since_datetime"),
    ],
)
def test_a_request_value_outside_the_contract_is_rejected(field: str, value) -> None:
    """Contract §1: `contract_version` and `k` are ints, `sources` a list of
    source names, `since` an ISO date or null. Coercing anything else, or
    reading an empty `sources` as "all", would widen a search silently."""
    body = {"contract_version": 2, "query": "rag", "caller": "mcp", field: value}
    assert _search(body).status_code == 422


def test_an_iso_date_since_is_accepted() -> None:
    """`since` arrives as an ISO date string in json (contract §1)."""
    resp = _search({"contract_version": 2, "query": "rag", "caller": "mcp", "since": "2026-10-01"})
    assert resp.status_code == 200


@pytest.mark.parametrize("since", ["2026-1-1", "２０２６-１０-０１", "20261001"])
def test_a_since_not_written_as_yyyy_mm_dd_is_rejected(since: str) -> None:
    resp = _search({"contract_version": 2, "query": "rag", "caller": "mcp", "since": since})
    assert resp.status_code == 422


def test_the_deadline_comes_from_deadline_ms_not_from_caller() -> None:
    """Contract §2 rule 10: `deadline_ms` (default 5000) bounds the search;
    `caller` is only a label. Read through the deadline a lane is given."""
    seen: dict[str, float] = {}

    async def lane(req: SearchRequest, embedding: list[float], deadline_at: float) -> list:
        seen[req.query] = deadline_at - asyncio.get_running_loop().time()
        return []

    async def embed(text: str, deadline_at: float) -> list[float]:
        return [0.0]

    with TestClient(create_app(lanes={"notes": lane}, embed=embed)) as client:
        for query, caller, extra in (
            ("voice-default", "voice", {}),
            ("mcp-default", "mcp", {}),
            ("mcp-2s", "mcp", {"deadline_ms": 2000}),
        ):
            body = {
                "contract_version": 2,
                "query": query,
                "caller": caller,
                "sources": ["notes"],
                **extra,
            }
            assert client.post("/v1/search", json=body).status_code == 200
    assert 4.5 < seen["voice-default"] <= 5.0
    assert 4.5 < seen["mcp-default"] <= 5.0
    assert 1.5 < seen["mcp-2s"] <= 2.0


def test_per_lane_limits_and_a_deadline_are_accepted() -> None:
    resp = _search(
        {
            "contract_version": 2,
            "query": "rag",
            "caller": "voice",
            "k_per_source": {"corpus": 5, "briefs": 3},
            "deadline_ms": 60000,
        }
    )
    assert resp.status_code == 200


def test_a_per_lane_limit_for_a_lane_not_searched_is_refused() -> None:
    """Contract §1: a `k_per_source` entry for a lane outside `sources` is
    refused rather than silently ignored."""
    resp = _search(
        {
            "contract_version": 2,
            "query": "rag",
            "caller": "voice",
            "sources": ["corpus"],
            "k_per_source": {"wiki": 3},
        }
    )
    assert resp.status_code == 422
