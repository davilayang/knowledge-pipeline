"""Per-task result caching for /v1/extract.

Per task, not per batch: a caller whose `followups` failed can re-request the
whole batch, and the three that already succeeded return without a model call.
"""

import hashlib
import json
from pathlib import Path
from typing import Any

from domains.fetches_store.sources import (
    extraction_cache_lookup,
    extraction_cache_upsert,
)


def cache_key(
    *,
    task: str,
    content: str,
    content_type: str,
    user_notes: str | None,
    prompt_sha256: str,
    provider: str,
    model: str,
    generation: dict[str, Any],
) -> str:
    """Compose the key over every input that changes what a task returns.

    Under-specifying this already bit the structurer, whose key omitted its hint
    context and served hits from before those hints changed. So everything
    enters: content, content type, reader's notes, the resolved prompt (as its
    sha, which covers system message, envelope and schema), provider and model
    separately, and `generation` — the token ceiling and reasoning effort, which
    are service constants a request-derived key would miss.
    """
    parts = json.dumps(
        {
            "task": task,
            "content_sha": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            "content_type": content_type,
            "user_notes": user_notes or "",
            "prompt_sha256": prompt_sha256,
            "provider": provider,
            "model": model,
            "generation": generation,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return "extract:v1:" + hashlib.sha256(parts.encode("utf-8")).hexdigest()


def read(*, db_path: Path, key: str) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """The cached `(payload, call)` pair for a key, or None on miss."""
    row = extraction_cache_lookup(db_path=db_path, cache_key=key)
    if row is None:
        return None
    return json.loads(row["payload_json"]), json.loads(row["call_json"])


def write(
    *,
    db_path: Path,
    key: str,
    task: str,
    payload: dict[str, Any],
    call: dict[str, Any],
    ttl_days: int,
) -> None:
    extraction_cache_upsert(
        db_path=db_path,
        cache_key=key,
        task=task,
        payload_json=json.dumps(payload),
        call_json=json.dumps(call),
        ttl_days=ttl_days,
    )
