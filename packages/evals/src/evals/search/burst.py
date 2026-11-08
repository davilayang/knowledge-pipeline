"""``eval-search-burst``: check that a burst of MCP searches cannot starve voice.

Fires one ``voice`` search and four concurrent ``mcp`` searches at a running searcher
and checks the hub search contract's admission rule: voice answers ``ok`` within its
5 s deadline, two MCP searches are answered and two are refused with 429.

    uv run eval-search-burst --searcher http://localhost:8000

Standard library only, so it also runs inside the searcher container, which publishes
no port:

    docker compose exec -T searcher python - < packages/evals/src/evals/search/burst.py

Exits non-zero when any check fails. The MCP searches start a moment before voice, so
they hold both admission slots while voice runs.
"""

import argparse
import json
import sys
import threading
import time
import urllib.error
import urllib.request

VOICE_DEADLINE_S = 5.0
MCP_SEARCHES = 4
MCP_ADMITTED = 2


def _post(searcher: str, body: dict) -> tuple[int, dict, float]:
    """Status, json body and seconds taken for one search."""
    req = urllib.request.Request(
        f"{searcher}/v1/search",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    began = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=70) as resp:
            status, payload = resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as err:
        status, payload = err.code, json.loads(err.read() or b"{}")
    return status, payload, time.perf_counter() - began


def burst(searcher: str, query: str) -> list[str]:
    """Run the burst; return the failed checks, empty when all pass."""
    results: dict[str, tuple[int, dict, float]] = {}
    gate = threading.Barrier(MCP_SEARCHES)

    def mcp(i: int) -> None:
        gate.wait()
        body = {"contract_version": 2, "query": query, "caller": "mcp", "deadline_ms": 60000}
        results[f"mcp{i}"] = _post(searcher, body)

    threads = [threading.Thread(target=mcp, args=(i,)) for i in range(MCP_SEARCHES)]
    for t in threads:
        t.start()
    time.sleep(0.05)
    results["voice"] = _post(searcher, {"contract_version": 2, "query": query, "caller": "voice"})
    for t in threads:
        t.join()

    for name, (status, payload, took) in sorted(results.items()):
        print(
            f"{name:6} {status} {payload.get('status', payload.get('detail', ''))!s:16} {took:.3f}s"
        )

    failed = []
    status, payload, took = results["voice"]
    if status != 200 or payload.get("status") != "ok":
        failed.append(f"voice answered {status} {payload.get('status')}, not 200 ok")
    if took >= VOICE_DEADLINE_S:
        failed.append(f"voice took {took:.3f}s, not under {VOICE_DEADLINE_S}s")
    codes = sorted(results[f"mcp{i}"][0] for i in range(MCP_SEARCHES))
    want = [200] * MCP_ADMITTED + [429] * (MCP_SEARCHES - MCP_ADMITTED)
    if codes != want:
        failed.append(f"mcp statuses {codes}, not {want}")
    return failed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--searcher", default="http://localhost:8000")
    parser.add_argument("--query", default="retrieval augmented generation")
    args = parser.parse_args(argv)
    failed = burst(args.searcher, args.query)
    for f in failed:
        print(f"FAIL {f}")
    print("PASS" if not failed else f"{len(failed)} check(s) failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
