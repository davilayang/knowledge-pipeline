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
