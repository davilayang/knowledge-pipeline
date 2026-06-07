"""GET /v1/canonicalize with url_aliases caching."""

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Query, Request

from fetcher.canonicalize import canonicalize
from fetcher.db import open_connection


router = APIRouter()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _iso_plus(days: int) -> str:
    return (
        datetime.now(timezone.utc) + timedelta(days=days)
    ).isoformat(timespec="seconds").replace("+00:00", "Z")


def _input_hash(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


@router.get("/v1/canonicalize")
async def canonicalize_endpoint(
    request: Request,
    url: str = Query(...),
    force_refresh: bool = Query(default=False),
) -> Any:
    settings = request.app.state.settings
    conn = open_connection(settings.db_path)
    try:
        key = _input_hash(url)
        if not force_refresh:
            row = conn.execute(
                """
                SELECT input_url, canonical_url, redirects_json, params_stripped,
                       fetched_at, expires_at
                  FROM url_aliases
                 WHERE input_url_hash = ?
                """,
                (key,),
            ).fetchone()
            if row is not None:
                if row[5] >= _now_iso():
                    return {
                        "input_url": row[0],
                        "canonical_url": row[1],
                        "redirects_followed": json.loads(row[2]),
                        "params_stripped": json.loads(row[3]),
                        "cache_hit": True,
                    }
                conn.execute("DELETE FROM url_aliases WHERE input_url_hash = ?", (key,))

        result = canonicalize(url)
        conn.execute(
            """
            INSERT INTO url_aliases (
                input_url_hash, input_url, canonical_url, redirects_json,
                params_stripped, fetched_at, expires_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(input_url_hash) DO UPDATE SET
                input_url = excluded.input_url,
                canonical_url = excluded.canonical_url,
                redirects_json = excluded.redirects_json,
                params_stripped = excluded.params_stripped,
                fetched_at = excluded.fetched_at,
                expires_at = excluded.expires_at
            """,
            (
                key,
                result.input_url,
                result.canonical_url,
                json.dumps(result.redirects_followed),
                json.dumps(result.params_stripped),
                _now_iso(),
                _iso_plus(settings.cache_ttl_days),
            ),
        )
        return {
            "input_url": result.input_url,
            "canonical_url": result.canonical_url,
            "redirects_followed": result.redirects_followed,
            "params_stripped": result.params_stripped,
            "cache_hit": False,
        }
    finally:
        conn.close()
