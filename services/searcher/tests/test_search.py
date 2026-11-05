"""POST /v1/search against the search-results contract (hub `contracts/search.md`)."""

import pytest
from fastapi.testclient import TestClient

from searcher.app import create_app


def _search(body: dict):
    with TestClient(create_app()) as client:
        return client.post("/v1/search", json=body)


def test_a_valid_request_is_answered_unavailable_with_every_lane_skipped() -> None:
    """Until lanes are wired, no lane runs, so status precedence rule 1 applies:
    no lane finished `ok`, so `unavailable` and no items."""
    resp = _search(
        {"contract_version": 1, "query": "rag", "caller": "mcp", "sources": ["corpus", "wiki"]}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["contract_version"] == 1
    assert body["status"] == "unavailable"
    assert body["items"] == []
    assert body["lanes"] == {
        "corpus": {"outcome": "skipped", "candidates": 0, "returned": 0},
        "wiki": {"outcome": "skipped", "candidates": 0, "returned": 0},
    }


def test_an_unknown_request_field_is_rejected() -> None:
    """A misspelt filter must never widen a search (contract §1)."""
    resp = _search({"contract_version": 1, "query": "rag", "caller": "mcp", "sorces": ["wiki"]})
    assert resp.status_code == 422


def test_a_contract_version_this_service_does_not_serve_is_refused() -> None:
    resp = _search({"contract_version": 2, "query": "rag", "caller": "mcp"})
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
        pytest.param("since", "2026-11-04T00:00:00", id="since_datetime"),
    ],
)
def test_a_request_value_outside_the_contract_is_rejected(field: str, value) -> None:
    """Contract §1: `contract_version` and `k` are ints, `sources` a list of
    source names, `since` an ISO date or null. Coercing anything else, or
    reading an empty `sources` as "all", would widen a search silently."""
    body = {"contract_version": 1, "query": "rag", "caller": "mcp", field: value}
    assert _search(body).status_code == 422


def test_an_iso_date_since_is_accepted() -> None:
    """`since` arrives as an ISO date string in json (contract §1)."""
    resp = _search({"contract_version": 1, "query": "rag", "caller": "mcp", "since": "2026-10-01"})
    assert resp.status_code == 200


@pytest.mark.parametrize("since", ["2026-1-1", "２０２６-１０-０１", "20261001"])
def test_a_since_not_written_as_yyyy_mm_dd_is_rejected(since: str) -> None:
    resp = _search({"contract_version": 1, "query": "rag", "caller": "mcp", "since": since})
    assert resp.status_code == 422


def test_voice_searches_get_a_deadline_and_mcp_searches_none() -> None:
    """The app's caller-to-deadline table, read through what a lane is given."""
    seen: dict[str, float | None] = {}

    async def lane(query: str, embedding: list[float], deadline_at: float | None) -> list:
        seen[query] = deadline_at
        return []

    async def embed(text: str) -> list[float]:
        return [0.0]

    with TestClient(create_app(lanes={"notes": lane}, embed=embed)) as client:
        for caller in ("voice", "mcp"):
            body = {"contract_version": 1, "query": caller, "caller": caller, "sources": ["notes"]}
            assert client.post("/v1/search", json=body).status_code == 200
    assert seen["voice"] is not None
    assert seen["mcp"] is None
