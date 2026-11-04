"""Read a run harvested from newsletter-assistant's own search tools.

Those runs predate `/v1/search`: per probe, each lane's raw chunk ranking
(`lanes`) and the MCP `search_knowledge` items (`shipped`), whose uris use the
retired per-kind schemes (`article://`, `session://transcript/`, `note://daily/`)
and the retired `articles` lane / `article` kind names, read here as `corpus`.
They are the baseline the ported search is compared against, so these input
names stay as recorded.
"""

from evals.search.score import Hit

# Voice `recall` reads the same lanes at these fixed depths (keys as recorded).
VOICE_DEPTH = {"articles": 5, "sessions": 3, "notes": 3, "briefs": 20}
_LANE_KIND = {"articles": "corpus", "sessions": "session", "notes": "note", "briefs": "brief"}


def _lane_key(lane: str, row: dict) -> str:
    if lane == "briefs":
        return row.get("source_ref", "")
    if lane == "sessions":
        return row.get("session_id") or row.get("content_id", "")
    return row.get("content_id", "")


def _shipped_hit(item: dict) -> Hit:
    uri, kind = item["uri"], item["kind"]
    if kind == "article":
        return Hit("corpus", uri.split("/", 3)[-1])  # article://<source>/<content_id>
    if kind == "session":
        return Hit("session", uri.rsplit("/", 1)[-1])
    if kind == "note":
        return Hit("note", "", item.get("snippet", ""), uri.rsplit("/", 1)[-1])
    return Hit(kind)


def hits_from_legacy_run(run: dict) -> dict[str, dict[int, list[Hit]]]:
    """`voice`: the lanes at voice depth; `mcp`: the all-sources `search_knowledge` items."""
    voice, mcp = {}, {}
    for pid, rec in run.items():
        voice[int(pid)] = [
            Hit(_LANE_KIND[lane], _lane_key(lane, r), r.get("chunk", ""), r.get("content_date", ""))
            for lane, depth in VOICE_DEPTH.items()
            for r in rec["lanes"][lane][:depth]
        ]
        mcp[int(pid)] = [_shipped_hit(it) for it in rec["shipped"]["all"]]
    return {"voice": voice, "mcp": mcp}
