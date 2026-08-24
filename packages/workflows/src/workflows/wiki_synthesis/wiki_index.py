"""Build the whole-wiki index sidecars from wiki.db — the producer side of the
newsletter-assistant bridge. Emits `_index/resolve.json` (resolution +
orientation, no claims) and `index.md` (human TOC). Dagster-free so it's
unit-testable and reusable by a backfill script; the Dagster asset is a thin
wrapper.
"""

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from domains.wiki.attributed import count_sources_for_entity, has_user_claims_for_entity
from domains.wiki.state import connection, get_all_aliases, get_all_pages

_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class BuildIndexResult:
    aliases_total: int
    pages_total: int
    resolve_written: bool
    index_written: bool


def _build_alias_map(pages, aliases) -> dict[str, str]:
    """Flat `key.lower() → entity_id` map: every entity_id self-mapped, every
    canonical name, every alias. A key already mapped to a DIFFERENT entity_id is
    a collision → ValueError (mirrors the global-unique alias constraint)."""
    m: dict[str, str] = {}

    def _set(key: str, eid: str) -> None:
        k = key.lower()
        if m.get(k, eid) != eid:
            raise ValueError(f"alias collision: {k!r} maps to both {m[k]} and {eid}")
        m[k] = eid

    for p in pages:
        _set(p.entity_id, p.entity_id)  # self-map (contract-critical)
        _set(p.canonical_name, p.entity_id)
    for alias, eid in aliases:
        _set(alias, eid)
    return m


def build_wiki_index(*, wiki_db_path: Path | str, wiki_dir: Path | str) -> BuildIndexResult:
    """Read wiki.db and write `_index/resolve.json` + `index.md` into `wiki_dir`."""
    wiki_dir = Path(wiki_dir)
    with connection(wiki_db_path) as conn:
        pages = get_all_pages(conn)
        aliases = get_all_aliases(conn)
        entities = {}
        for p in pages:
            # `has_user_claims` names what the flag means; `has_derived` is the
            # legacy name, written alongside it for one release so a consumer
            # rollback still finds the key. Purely additive, which is why
            # `_SCHEMA_VERSION` deliberately stays 1: bumping it while both keys
            # exist buys nothing, and a consumer pinned to schema 1 rejects the
            # WHOLE file on an unrecognised version. The bump belongs with the
            # removal of `has_derived` — the change that truly breaks an old
            # reader.
            has_user = has_user_claims_for_entity(conn, p.entity_id)
            entities[p.entity_id] = {
                "name": p.canonical_name,
                "type": p.entity_type,
                "file": p.file_path,
                "num_sources": count_sources_for_entity(conn, p.entity_id),
                "has_user_claims": has_user,
                "has_derived": has_user,
                "page_hash": _sha256((wiki_dir / p.file_path).read_bytes()),
            }

    alias_map = _build_alias_map(pages, aliases)
    # snapshot_id fingerprints the WHOLE resolve payload (aliases + every entity
    # field), not the wall clock — so an unchanged rebuild is a no-op while any
    # change (a page edit, but also an alias-only merge/reject or a num_sources
    # shift) bumps it and forces a rewrite. Consumers pin it as the snapshot.
    content = {"schema_version": _SCHEMA_VERSION, "aliases": alias_map, "entities": entities}
    snapshot_id = _sha256(json.dumps(content, sort_keys=True, ensure_ascii=False).encode())
    resolve = {**content, "generated_at": datetime.now(UTC).isoformat(), "snapshot_id": snapshot_id}

    index_dir = wiki_dir / "_index"
    index_dir.mkdir(parents=True, exist_ok=True)
    # index.md first, resolve.json LAST: resolve.json is the commit marker a
    # consumer keys on, so it must never point past what's already on disk.
    index_written = _write_if_changed(wiki_dir / "index.md", _render_index_md(pages))

    # resolve.json carries a generated_at timestamp, so raw byte-equality would
    # churn every tick — skip on the content fingerprint (snapshot_id) instead.
    resolve_path = index_dir / "resolve.json"
    resolve_written = _existing_snapshot_id(resolve_path) != snapshot_id
    if resolve_written:
        _write_atomic(
            resolve_path,
            json.dumps(resolve, sort_keys=True, indent=2, ensure_ascii=False) + "\n",
        )

    return BuildIndexResult(
        aliases_total=len(alias_map),
        pages_total=len(pages),
        resolve_written=resolve_written,
        index_written=index_written,
    )


def _existing_snapshot_id(path: Path) -> str | None:
    """The snapshot_id in an existing resolve.json, or None if absent/unreadable."""
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("snapshot_id")
    except (FileNotFoundError, ValueError):
        return None


def _write_if_changed(path: Path, text: str) -> bool:
    """Atomic write unless the file already holds exactly these bytes. Returns
    whether it wrote (a missing file always writes — self-heals)."""
    try:
        if path.read_text(encoding="utf-8") == text:
            return False
    except FileNotFoundError:
        pass
    _write_atomic(path, text)
    return True


def _render_index_md(pages) -> str:
    """Human TOC grouped by the live entity_type values (no hardcoded set),
    each link labelled by canonical name → on-disk file."""
    by_type: dict[str, list] = {}
    for p in pages:
        by_type.setdefault(p.entity_type, []).append(p)

    lines = ["# Wiki Index", "", f"Total pages: {len(pages)}", ""]
    for entity_type in sorted(by_type):
        lines.append(f"## {entity_type.title()}")
        lines.append("")
        for p in sorted(by_type[entity_type], key=lambda p: p.canonical_name):
            lines.append(f"- [{p.canonical_name}]({p.file_path})")
        lines.append("")
    return "\n".join(lines)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_atomic(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
