"""``eval-search-port-check``: compare each searcher lane with newsletter-assistant's,
probe by probe, from a harvest (format v2) of newsletter-assistant's results taken at the
same moment against the same Chroma and data. Each lane is searched on its own and
compared with the lane's reference caller in newsletter-assistant: MCP for corpus,
sessions and notes, voice for briefs and wiki.

    uv run eval-search-port-check --harvest data/eval_runs/search/harvest.json \
        --searcher http://localhost:8001 [--lanes sessions notes]

Exits non-zero on any failure. Tie swaps and blank titles replaced by the url pass and are printed.
"""

import argparse
import json
import sys
from pathlib import Path
from urllib.parse import unquote

import httpx

from evals.core.manifest import RunManifest, code_rev, format_manifest_line
from evals.search.cli import DATASETS
from evals.search.dataset import load_probes

TIE_GAP = 1e-4
# Voice reads 5 corpus chunks; MCP defaults to k=10.
DEPTHS = [5, 10]
FORMAT_VERSION = 2
LANES = ("corpus", "sessions", "notes", "briefs", "wiki")
# Lanes whose items are matched passages; briefs and wiki items need none.
PASSAGE_LANES = ("corpus", "sessions", "notes")


def _uri_id(uri: str) -> str:
    """The id of a `kos://piece/<kind>/<id>` uri, decoded."""
    return unquote(uri.split("/", 4)[-1])


def _chunks_of(lane: str, uri: str, chunks: list[dict]) -> list[dict]:
    """The harvested chunks that belong to the item at `uri`.

    Notes are indexed under `local:<note_id>.md`. A brief's file is
    `<date>_<slug>-<first 8 chars of brief_id>.md`, so it is matched on that suffix.
    """
    i = _uri_id(uri)
    if lane == "notes":
        return [c for c in chunks if c["source_ref"] == f"local:{i}.md"]
    if lane == "briefs":
        return [c for c in chunks if c["source_ref"].removesuffix(".md").endswith(f"-{i[:8]}")]
    key = {"corpus": "content_id", "sessions": "session_id", "wiki": "entity_id"}[lane]
    return [c for c in chunks if c[key] == i]


def compare(lane: str, record: dict, response: dict) -> tuple[list[str], list[str]]:
    """Return (failures, listed differences) for one lane of one probe at one depth.

    Listed differences pass but are always reported: tie swaps, and a blank
    newsletter-assistant title the searcher replaced with the item's url.

    Order matches when every pair the searcher returns in the opposite order to
    newsletter-assistant was tied there (best chunk distances within `TIE_GAP`);
    each such pair is returned as a tie swap, never passed silently. Every field
    the harvested item carries, and every key of its `meta`, must be equal; a field
    newsletter-assistant does not produce is absent from the harvest and unchecked.
    """
    failures: list[str] = []
    ties: list[str] = []
    expected = "ok" if record["items"] else "nothing_matched"
    status_failure = []
    if response["status"] != expected:
        status_failure = [f"status: searcher {response['status']!r}, expected {expected!r}"]

    def best(uri: str) -> float | None:
        ds = [c["distance"] for c in _chunks_of(lane, uri, record["chunks"])]
        return min(ds) if ds else None

    want = [it["uri"] for it in record["items"]]
    got = [it["uri"] for it in response["items"]]
    dupes = sorted({u for u in got if got.count(u) > 1})
    if dupes:
        failures.append(f"duplicate: searcher returned {dupes} more than once")
        got = list(dict.fromkeys(got))
    if got != want and sorted(got) == sorted(want):
        pos = {u: i for i, u in enumerate(got)}
        for i, a in enumerate(want):
            for b in want[i + 1 :]:
                if pos[b] < pos[a]:
                    da, db = best(a), best(b)
                    if da is None or db is None:
                        failures.append(f"{a} and {b} swapped, distance unknown")
                        continue
                    gap = abs(da - db)
                    pair = f"{a} and {b} swapped, gap {gap:.5f}"
                    (ties if gap < TIE_GAP else failures).append(pair)
        failures = [f if f.startswith("duplicate:") else f"order: {f}" for f in failures]
    elif sorted(got) != sorted(want):
        decoded = sorted(map(_uri_id, got)) == sorted(map(_uri_id, want))
        what = "uri encoding" if decoded else "pieces"
        only_got = [u for u in got if u not in want]
        only_want = [u for u in want if u not in got]
        failures.append(
            f"{what} differ: searcher only {only_got}, newsletter-assistant only {only_want}"
        )
    na = {it["uri"]: it for it in record["items"]}
    for it in response["items"]:
        if it["uri"] not in na:
            continue
        want_it = na[it["uri"]]
        for field in want_it.keys() - {"uri", "passages", "meta"}:
            if field == "title" and want_it["title"] == "" and want_it.get("url") is not None:
                if it.get("title") == want_it["url"]:
                    # The contract turns a blank stored title into the url.
                    ties.append(
                        f"title: {it['uri']} blank in newsletter-assistant, searcher uses its url"
                    )
                    continue
            if it.get(field) != want_it[field]:
                failures.append(
                    f"{field} of {it['uri']}: searcher {it.get(field)!r}, "
                    f"newsletter-assistant {want_it[field]!r}"
                )
        for key, value in want_it.get("meta", {}).items():
            if it.get("meta", {}).get(key) != value:
                failures.append(
                    f"meta.{key} of {it['uri']}: searcher {it.get('meta', {}).get(key)!r}, "
                    f"newsletter-assistant {value!r}"
                )
        chunks = [c["chunk"] for c in _chunks_of(lane, it["uri"], record["chunks"])]
        if not it.get("passages") and lane in PASSAGE_LANES:
            failures.append(f"{it['uri']} has no passages")
        for p in it.get("passages") or []:
            if not p:
                failures.append(f"{it['uri']} has an empty passage")
            # A passage is one whole chunk; 3d loosens this when it sets per-caller length.
            elif p not in chunks:
                failures.append(f"passage of {it['uri']} is not a whole chunk: {p[:80]!r}")
    return status_failure + failures, ties


def _search(searcher: str, query: str, k: int, lane: str) -> dict:
    """The one network call: an MCP search of one lane at depth `k`."""
    body = {
        "contract_version": 1,
        "query": query,
        "caller": "mcp",
        "k": k,
        "sources": [lane],
    }
    resp = httpx.post(f"{searcher}/v1/search", json=body, timeout=60)
    resp.raise_for_status()
    return resp.json()


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--harvest", type=Path, required=True)
    ap.add_argument("--searcher", default="http://localhost:8001")
    ap.add_argument("--probes", type=Path, default=DATASETS / "probes.jsonl")
    ap.add_argument("--lanes", nargs="+", choices=LANES, default=list(LANES))
    args = ap.parse_args(argv)

    harvest = json.loads(args.harvest.read_text())
    if harvest.get("format_version") != FORMAT_VERSION:
        sys.exit(f"harvest format_version {harvest.get('format_version')} is not {FORMAT_VERSION}")
    if harvest["depths"] != DEPTHS:
        sys.exit(f"harvest depths {harvest['depths']} are not {DEPTHS}")
    head, probes = load_probes(args.probes)
    manifest = RunManifest(
        dataset=args.probes.name,
        dataset_schema=1,
        subject=f"searcher {args.searcher}",
        subject_model="none",
        judge_model=None,
        code_rev=code_rev(),
        mode="gate",
        runs=1,
    )
    print(
        f"{format_manifest_line(manifest)} dataset_version={head.get('dataset_version')} "
        f"harvested_at={harvest.get('harvested_at')}"
    )
    answerable = {str(p.probe_id) for p in probes if not p.negative}
    if set(harvest["probes"]) != answerable:
        sys.exit(
            f"harvest probes {sorted(harvest['probes'], key=int)} differ from the answerable ones"
        )

    failed = 0
    for pid, probe in sorted(harvest["probes"].items(), key=lambda kv: int(kv[0])):
        for depth in harvest["depths"]:
            for lane in args.lanes:
                record = probe["depths"][str(depth)]["lanes"].get(lane)
                if record is None:
                    failures, ties = [f"harvest has no {lane} lane"], []
                else:
                    try:
                        resp = _search(args.searcher, probe["ask"], depth, lane)
                    except (httpx.HTTPError, ValueError) as e:
                        failures, ties = [f"searcher request: {e!r}"], []
                    else:
                        failures, ties = compare(lane, record, resp)
                failed += bool(failures)
                print(f"probe {pid} depth {depth} {lane}: {'FAIL' if failures else 'ok'}")
                for line in failures:
                    print(f"  fail: {line}")
                for line in ties:
                    print(f"  listed: {line}")
    total = len(harvest["probes"]) * len(harvest["depths"]) * len(args.lanes)
    print(f"{total - failed} of {total} passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
