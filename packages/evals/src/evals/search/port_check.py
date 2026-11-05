"""``eval-search-port-check``: compare the searcher's corpus lane with
newsletter-assistant's, probe by probe, from a harvest of newsletter-assistant's
results taken at the same moment against the same Chroma and `corpus.db`.

    uv run eval-search-port-check --harvest data/eval_runs/search/harvest.json \
        --searcher http://localhost:8001

Exits non-zero on any failure. Tie swaps pass and are printed.
"""

import argparse
import json
import sys
from pathlib import Path

import httpx

from evals.core.manifest import RunManifest, code_rev, format_manifest_line
from evals.search.cli import DATASETS
from evals.search.dataset import load_probes
from evals.search.score import corpus_content_id

TIE_GAP = 1e-4
FIELDS = ("title", "author", "url", "date")
# Voice reads 5 corpus chunks; MCP defaults to k=10.
DEPTHS = [5, 10]


def compare(record: dict, response: dict) -> tuple[list[str], list[str]]:
    """Return (failures, tie swaps) for one probe at one depth.

    Order matches when every pair the searcher returns in the opposite order to
    newsletter-assistant was tied there (best chunk distances within `TIE_GAP`);
    each such pair is returned as a tie swap, never passed silently.
    """
    failures: list[str] = []
    ties: list[str] = []
    expected = "ok" if record["items"] else "nothing_matched"
    status_failure = []
    if response["status"] != expected:
        status_failure = [f"status: searcher {response['status']!r}, expected {expected!r}"]
    best: dict[str, float] = {}
    for c in record["chunks"]:
        best[c["content_id"]] = min(c["distance"], best.get(c["content_id"], c["distance"]))
    want = [it["uri"] for it in record["items"]]
    got = [it["uri"] for it in response["items"]]
    if got != want and sorted(got) == sorted(want):
        pos = {u: i for i, u in enumerate(got)}
        for i, a in enumerate(want):
            for b in want[i + 1 :]:
                if pos[b] < pos[a]:
                    da, db = best.get(corpus_content_id(a)), best.get(corpus_content_id(b))
                    if da is None or db is None:
                        failures.append(f"{a} and {b} swapped, distance unknown")
                        continue
                    gap = abs(da - db)
                    pair = f"{a} and {b} swapped, gap {gap:.5f}"
                    (ties if gap < TIE_GAP else failures).append(pair)
        failures = [f"order: {f}" for f in failures]
    elif sorted(got) != sorted(want):
        decoded = sorted(map(corpus_content_id, got)) == sorted(map(corpus_content_id, want))
        what = "uri encoding" if decoded else "pieces"
        failures.append(f"{what} differ: searcher {got}, newsletter-assistant {want}")
    na = {it["uri"]: it for it in record["items"]}
    for it in response["items"]:
        if it["uri"] not in na:
            continue
        for field in FIELDS:
            if it.get(field) != na[it["uri"]][field]:
                failures.append(
                    f"{field} of {it['uri']}: searcher {it.get(field)!r}, "
                    f"newsletter-assistant {na[it['uri']][field]!r}"
                )
        chunks = [
            c["chunk"] for c in record["chunks"] if c["content_id"] == corpus_content_id(it["uri"])
        ]
        if not it["passages"]:
            failures.append(f"{it['uri']} has no passages")
        for p in it["passages"]:
            if not p:
                failures.append(f"{it['uri']} has an empty passage")
            # A 3b passage is one whole chunk; 3d loosens this when it sets per-caller length.
            elif p not in chunks:
                failures.append(f"passage of {it['uri']} is not a whole chunk: {p[:80]!r}")
    return status_failure + failures, ties


def _search(searcher: str, query: str, k: int) -> dict:
    """The one network call: a corpus-only MCP search at depth `k`."""
    body = {
        "contract_version": 1,
        "query": query,
        "caller": "mcp",
        "k": k,
        "sources": ["corpus"],
    }
    resp = httpx.post(f"{searcher}/v1/search", json=body, timeout=60)
    resp.raise_for_status()
    return resp.json()


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--harvest", type=Path, required=True)
    ap.add_argument("--searcher", default="http://localhost:8001")
    ap.add_argument("--probes", type=Path, default=DATASETS / "probes.jsonl")
    args = ap.parse_args(argv)

    harvest = json.loads(args.harvest.read_text())
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
            try:
                resp = _search(args.searcher, probe["ask"], depth)
            except (httpx.HTTPError, ValueError) as e:
                failures, ties = [f"searcher request: {e!r}"], []
            else:
                failures, ties = compare(probe["depths"][str(depth)], resp)
            failed += bool(failures)
            print(f"probe {pid} depth {depth}: {'FAIL' if failures else 'ok'}")
            for line in failures:
                print(f"  fail: {line}")
            for line in ties:
                print(f"  tie: {line}")
    total = len(harvest["probes"]) * len(harvest["depths"])
    print(f"{total - failed} of {total} passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
