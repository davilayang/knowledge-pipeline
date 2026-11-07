"""``eval-search-port-check``: compare each searcher lane with newsletter-assistant's,
probe by probe, from a harvest (format v2) of newsletter-assistant's results taken at the
same moment against the same Chroma and data. Each lane is searched on its own and
compared with the lane's reference in newsletter-assistant: its MCP search for corpus,
sessions and notes, and its voice logic for briefs and wiki.

    uv run eval-search-port-check --harvest data/eval_runs/search/harvest.json \
        --searcher http://localhost:8001 [--lanes sessions notes]

`--lanes` defaults to all five; a lane the searcher has not wired yet fails until it is.

Exits non-zero on any failure. Tie swaps, one-for-one swaps of pieces tied at the depth cut
(any lane), repeated reference pieces the searcher merged, and blank titles the searcher
replaced pass and are printed.
"""

import argparse
import json
import sys
from collections.abc import Callable
from pathlib import Path
from urllib.parse import unquote

import httpx

from evals.core.manifest import RunManifest, code_rev, format_manifest_line
from evals.search.cli import DATASETS
from evals.search.dataset import load_probes

# The two sides embed the query separately; that alone moves distances by up to 1.1e-3.
TIE_GAP = 2e-3
# Fixed for every lane. Voice's own per-lane counts (briefs 20 searched and 3 shown,
# wiki 3) are caller settings, checked when the callers switch over, not here.
DEPTHS = [5, 10]
FORMAT_VERSION = 2
LANES = ("corpus", "sessions", "notes", "briefs", "wiki")
# Lanes whose items are matched passages; briefs and wiki items need none.
PASSAGE_LANES = ("corpus", "sessions", "notes")
# Every harvested item must carry these, and the searcher's item must equal it on each.
FIELDS = {
    "corpus": ("title", "author", "url", "date"),
    "sessions": ("date", "summary"),
    "notes": ("title", "date"),
    "briefs": ("title", "date", "meta.status"),
    "wiki": (
        "title",
        "summary",
        "meta.num_sources",
        "meta.stale",
        "meta.related",
        "meta.has_user_notes",
    ),
}
_MISSING = object()


def _get(item: dict, field: str) -> object:
    """`field`'s value in `item`, reading `meta.<key>` inside `meta`; `_MISSING` if absent."""
    if field.startswith("meta."):
        return item.get("meta", {}).get(field.removeprefix("meta."), _MISSING)
    return item.get(field, _MISSING)


def _uri_id(uri: str) -> str:
    """The id of a `kos://piece/<kind>/<id>` uri, decoded."""
    return unquote(uri.split("/", 4)[-1])


def _chunks_of(lane: str, uri: str, chunks: list[dict]) -> list[dict]:
    """The harvested chunks that belong to the item at `uri`.

    Notes are indexed under `local:<note_id>.md`; the other lanes carry the id itself.
    """
    i = _uri_id(uri)
    if lane == "notes":
        return [c for c in chunks if c["source_ref"] == f"local:{i}.md"]
    key = {
        "corpus": "content_id",
        "sessions": "session_id",
        "briefs": "brief_id",
        "wiki": "entity_id",
    }[lane]
    return [c for c in chunks if c[key] == i]


def _tied_at_cut_off(
    only_got: list[str],
    only_want: list[str],
    want: list[str],
    best: Callable[[str], float | None],
    got_items: list[dict],
) -> bool:
    """Whether the two sides swapped pieces tied at the depth cut, in any lane: each
    kept as many pieces the other did not (a real Chroma tie trades one piece for
    another, it never drops or adds one), and every such piece sits within
    `TIE_GAP` of the last piece newsletter-assistant kept. Reference distances are
    the best harvested chunk's; the searcher's are `1 - matched_by.score`."""
    if not only_got or len(only_got) != len(only_want):
        return False
    kept = [d for u in want if (d := best(u)) is not None]
    if not kept:
        return False
    cut = max(kept)
    score = {it["uri"]: it.get("matched_by", [{}])[0].get("score") for it in got_items}
    distances = [best(u) for u in only_want]
    distances += [None if score.get(u) is None else 1 - score[u] for u in only_got]
    return all(d is not None and abs(d - cut) < TIE_GAP for d in distances)


def compare(lane: str, record: dict, response: dict) -> tuple[list[str], list[str]]:
    """Return (failures, listed differences) for one lane of one probe at one depth.

    Listed differences pass but are always reported: tie swaps; in any lane, a
    one-for-one swap of pieces tied at the depth cut; a piece newsletter-assistant
    repeats that the searcher merged; and a blank newsletter-assistant title the
    searcher replaced with the item's url (corpus), file name (notes, briefs) or
    resolve name or id (wiki).

    Order matches when every pair the searcher returns in the opposite order to
    newsletter-assistant was tied there (best chunk distances within `TIE_GAP`);
    each such pair is returned as a tie swap, never passed silently. Each of the
    lane's `FIELDS` must be in the harvested item and equal in the searcher's, checked
    in `FIELDS` order.
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
    for uri in sorted({u for u in want if want.count(u) > 1}):
        # newsletter-assistant's harvest can emit one item per Chroma hit, while a lane
        # merges a piece's hits into one item, as the contract requires.
        ties.append(f"newsletter-assistant repeats {uri}; searcher merged it")
    want = list(dict.fromkeys(want))
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
        differ = f"{what} differ: searcher only {only_got}, newsletter-assistant only {only_want}"
        if not decoded and _tied_at_cut_off(only_got, only_want, want, best, response["items"]):
            ties.append(f"cut-off: {differ}, all tied with the last kept piece")
        else:
            failures.append(differ)
    na = {it["uri"]: it for it in record["items"]}
    # A duplicate is reported once, above; only its first occurrence is compared.
    first: dict[str, dict] = {}
    for it in response["items"]:
        first.setdefault(it["uri"], it)
    for it in first.values():
        if it["uri"] not in na:
            continue
        want_it = na[it["uri"]]
        for field in FIELDS[lane]:
            value = _get(want_it, field)
            if value is _MISSING:
                failures.append(f"harvest item {it['uri']} lacks {field}")
                continue
            got_value = _get(it, field)
            if field == "title" and value == "" and want_it.get("url") is not None:
                if got_value == want_it["url"]:
                    # The contract turns a blank stored title into the url.
                    ties.append(
                        f"title: {it['uri']} blank in newsletter-assistant, searcher uses its url"
                    )
                    continue
            if field == "title" and value == "" and lane == "wiki" and got_value:
                # The contract never shows a blank title: the searcher falls back to
                # the entity's resolve name, then its id. The harvest carries no
                # entity name to check against, so any replacement is listed, never
                # passed silently.
                ties.append(
                    f"title: {it['uri']} blank in newsletter-assistant, "
                    "searcher uses its resolve name or id"
                )
                continue
            if field == "title" and value == "" and lane in ("notes", "briefs"):
                files = {
                    c["source_ref"].removeprefix("local:")
                    for c in _chunks_of(lane, it["uri"], record["chunks"])
                }
                files |= {f.removesuffix(".md") for f in files}
                if got_value in files:
                    # A note or brief with no title is shown by its file name, with or
                    # without `.md`: the lanes have not fixed which form yet.
                    ties.append(
                        f"title: {it['uri']} blank in newsletter-assistant, "
                        "searcher uses its filename"
                    )
                    continue
            if got_value != value:
                shown = None if got_value is _MISSING else got_value
                failures.append(
                    f"{field} of {it['uri']}: searcher {shown!r}, newsletter-assistant {value!r}"
                )
        chunks = [c["chunk"] for c in _chunks_of(lane, it["uri"], record["chunks"])]
        if not it.get("passages") and lane in PASSAGE_LANES:
            failures.append(f"{it['uri']} has no passages")
        for p in it.get("passages") or []:
            if not p:
                failures.append(f"{it['uri']} has an empty passage")
            # A passage must equal one whole harvested chunk until per-caller passage length is set.
            elif p not in chunks:
                failures.append(f"passage of {it['uri']} is not a whole chunk: {p[:80]!r}")
    return status_failure + failures, ties


def _search(searcher: str, query: str, k: int, lane: str) -> dict:
    """The one network call: a search of one lane at depth `k`."""
    # Lanes and `k` are given explicitly, so the reference does not depend on the caller;
    # `mcp` has no deadline, so no answer is cut short. Per-caller passage length is not
    # checked until it is set.
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
