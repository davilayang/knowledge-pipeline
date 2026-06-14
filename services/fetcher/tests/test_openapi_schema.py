"""Regression-pin the FastAPI `/openapi.json` shape.

Phase G enriches the auto-generated API docs (title, summary, description,
per-route tags, typed error envelope, etc.). These tests catch silent drift
where a future edit drops the operator-facing metadata.
"""

from fastapi.testclient import TestClient

from fetcher.app import create_app


def _openapi_schema() -> dict:
    """Return the live /openapi.json the FastAPI app generates."""
    app = create_app()
    with TestClient(app) as client:
        resp = client.get("/openapi.json")
    assert resp.status_code == 200
    return resp.json()


def test_openapi_info_carries_app_level_metadata(monkeypatch, tmp_db_path: str) -> None:
    """Operators landing on /docs see the service name, a one-line summary,
    and a non-trivial description — not just 'fetcher 0.1.0'."""
    monkeypatch.setenv("FETCHER_DB_PATH", tmp_db_path)
    monkeypatch.setenv("FETCHER_SOCKS5_URL", "socks5://x")
    monkeypatch.setenv("FETCHER_LLAMA_PARSE_API_KEY", "x")

    schema = _openapi_schema()
    info = schema["info"]
    assert info["title"] == "kp-fetcher"
    assert info.get("summary"), "info.summary must be present (FastAPI 0.99+)"
    assert info.get("description"), "info.description must be present"


def test_openapi_declares_four_tag_groups(monkeypatch, tmp_db_path: str) -> None:
    """/docs groups endpoints under Health / Fetch / Normalize / Utilities
    instead of showing one flat unsorted list."""
    monkeypatch.setenv("FETCHER_DB_PATH", tmp_db_path)
    monkeypatch.setenv("FETCHER_SOCKS5_URL", "socks5://x")
    monkeypatch.setenv("FETCHER_LLAMA_PARSE_API_KEY", "x")

    schema = _openapi_schema()
    tag_names = {tag["name"] for tag in schema.get("tags") or []}
    assert {"Health", "Fetch", "Normalize", "Utilities"} <= tag_names


def test_every_route_carries_a_non_empty_tag(monkeypatch, tmp_db_path: str) -> None:
    """Catches future endpoints that ship without slotting into one of the
    four tag groups (untagged routes show up under a hidden 'default'
    section in /docs)."""
    monkeypatch.setenv("FETCHER_DB_PATH", tmp_db_path)
    monkeypatch.setenv("FETCHER_SOCKS5_URL", "socks5://x")
    monkeypatch.setenv("FETCHER_LLAMA_PARSE_API_KEY", "x")

    schema = _openapi_schema()
    untagged = []
    for path, methods in schema["paths"].items():
        for method, operation in methods.items():
            if method == "parameters":
                continue
            if not operation.get("tags"):
                untagged.append(f"{method.upper()} {path}")
    assert not untagged, f"routes without a tag: {untagged}"


def test_every_route_carries_a_descriptive_summary(monkeypatch, tmp_db_path: str) -> None:
    """FastAPI auto-derives `summary` from the function name (title-cased), so
    a missing explicit `summary=` shows up as e.g. 'Structure Transcript
    Endpoint' — short, not descriptive. Require ≥30 chars + ≥4 words to force
    a real sentence."""
    monkeypatch.setenv("FETCHER_DB_PATH", tmp_db_path)
    monkeypatch.setenv("FETCHER_SOCKS5_URL", "socks5://x")
    monkeypatch.setenv("FETCHER_LLAMA_PARSE_API_KEY", "x")

    schema = _openapi_schema()
    weak = []
    for path, methods in schema["paths"].items():
        for method, operation in methods.items():
            if method == "parameters":
                continue
            summary = operation.get("summary") or ""
            if len(summary) < 30 or len(summary.split()) < 4:
                weak.append(f"{method.upper()} {path}: {summary!r}")
    assert not weak, f"routes with weak/auto-derived summary: {weak}"


def test_openapi_includes_problem_response_schema(monkeypatch, tmp_db_path: str) -> None:
    """Today's error responses are returned via `problem_response()` as
    untyped JSON. Without a pydantic model wired into `responses={...}` on
    each endpoint, /docs shows error envelopes as opaque `application/json`.
    This test pins that ProblemResponse is registered in the components
    schema once at least one route declares it."""
    monkeypatch.setenv("FETCHER_DB_PATH", tmp_db_path)
    monkeypatch.setenv("FETCHER_SOCKS5_URL", "socks5://x")
    monkeypatch.setenv("FETCHER_LLAMA_PARSE_API_KEY", "x")

    schema = _openapi_schema()
    components = schema.get("components") or {}
    schemas = components.get("schemas") or {}
    assert "ProblemResponse" in schemas, (
        f"ProblemResponse missing from openapi components.schemas; "
        f"found: {sorted(schemas.keys())}"
    )
