"""Fail-closed checks run before any search scoring."""

from urllib.parse import unquote

from evals.search.dataset import GoldNeed, Probe
from evals.search.score import SNAPSHOT_LANES, Snapshot, article_uri, session_uri, snapshot_digests

_CANONICAL = {"article": article_uri, "session": session_uri}


def validate(
    probes: list[Probe], gold: list[GoldNeed], snapshot: Snapshot, *, manifest: dict | None = None
) -> None:
    """Raise ValueError on the first row or snapshot lane that cannot back a score.

    Each need names a real probe, has grade 1 or 2, a unique id and at least one
    uri. Each uri is an article or session uri in canonical encoding (the form
    the scorer produces, so it can match) that the snapshot holds: an article if
    the corpus holds it or a snapshot session fetched it (a fetched id can differ
    from the stored corpus row), a session if `sessions.db` holds it. Negative
    probes carry no gold. Within a probe an article belongs to one need; a
    session may belong to several, because one roundup session delivers several
    gold pieces. With a manifest, it names every lane and each lane's content
    digest matches.
    """
    if manifest is not None:
        if set(manifest["digests"]) != set(SNAPSHOT_LANES):
            raise ValueError(
                f"manifest digests must name exactly the lanes {sorted(SNAPSHOT_LANES)}"
            )
        actual = snapshot_digests(snapshot.root)
        for lane, digest in manifest["digests"].items():
            if actual[lane] != digest:
                raise ValueError(f"snapshot lane {lane!r} does not match the manifest digest")
    probe_ids = {p.probe_id for p in probes}
    negatives = {p.probe_id for p in probes if p.negative}
    seen: set[str] = set()
    owner: dict[tuple[int, str], str] = {}
    for g in gold:
        if g.gold_id in seen:
            raise ValueError(f"gold_id {g.gold_id} is repeated")
        seen.add(g.gold_id)
        if g.probe_id not in probe_ids:
            raise ValueError(f"{g.gold_id}: no such probe {g.probe_id}")
        if g.probe_id in negatives:
            raise ValueError(f"{g.gold_id}: probe {g.probe_id} is negative and carries gold")
        if g.grade not in (1, 2):
            raise ValueError(f"{g.gold_id}: grade must be 1 or 2, got {g.grade!r}")
        if not g.uris:
            raise ValueError(f"{g.gold_id}: no uris")
        for uri in g.uris:
            kind, _, ident = uri.removeprefix("kos://piece/").partition("/")
            ident = unquote(ident)
            if kind not in _CANONICAL or _CANONICAL[kind](ident) != uri:
                raise ValueError(f"{g.gold_id}: {uri} is not a canonical article or session uri")
            if kind == "article":
                ok = ident in snapshot.content_ids or ident in snapshot.fetched_ids
                prev = owner.setdefault((g.probe_id, uri), g.gold_id)
                if prev != g.gold_id:
                    raise ValueError(
                        f"{uri} is in two needs of probe {g.probe_id}: {prev}, {g.gold_id}"
                    )
            else:
                ok = ident in snapshot.session_ids
            if not ok:
                raise ValueError(f"{g.gold_id}: {uri} does not resolve on the snapshot")
