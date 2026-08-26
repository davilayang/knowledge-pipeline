"""Lexical fidelity scorer + A/B runner for the article structurer.

`POST /v1/structure` cleans a user-pasted article body through a cloud LLM. Its
one hard requirement is that it removes boilerplate without rewriting the
article, and the way it fails is by quietly summarising instead — merging the
author's sentences and dropping code blocks partway through a long document.

The scorer measures that as **trigram recall**: the share of the raw input's
three-word sequences that still appear somewhere in the structured output.
Paraphrasing destroys trigrams, so a rewrite scores low even when the output
reads well and keeps every heading.

Two things the score is *not*. It counts correctly-deleted boilerplate as loss,
because the denominator is the whole raw input — so every source has its own
floor set by how much navigation chrome it carries, and a score is only
meaningful against the same fixture under a different prompt, never against a
different fixture. And membership is position-blind: it asks whether wording
survived, not whether it stayed in place.

Nothing in `packages/evals` covers this. Those harnesses score extraction
against `queue_items.raw_content`, which is already the structurer's output --
so text this stage drops is invisible to them.
"""

import argparse
import asyncio
import hashlib
import json
import os
import re
import sqlite3
from pathlib import Path

from fetcher.extractors._cloud_chain import call_cloud_chain
from fetcher.extractors.structure import get_chain


def _tokens(text: str) -> list[str]:
    return re.sub(r"[^a-z0-9 ]", " ", text.lower()).split()


def _trigrams(text: str) -> list[tuple[str, str, str]]:
    t = _tokens(text)
    return list(zip(t, t[1:], t[2:], strict=False))


def _line_tallies(source: str, structured: str) -> list[tuple[int, int]]:
    """Per source line, (surviving trigrams, total trigrams), in document order.

    Counting per line rather than across the whole text keeps trigrams that
    straddle a line break out of the denominator: the structurer reflows
    paragraphs, so those would miss on every run and add a constant noise floor.
    Lines too short to form a trigram contribute nothing.
    """
    produced = set(_trigrams(structured))
    tallies = []
    for line in source.split("\n"):
        trigrams = _trigrams(line)
        if not trigrams:
            continue
        tallies.append((sum(1 for t in trigrams if t in produced), len(trigrams)))
    return tallies


def trigram_recall(source: str, structured: str) -> float:
    """Share of the source's word-trigrams that survive into the structured text.

    The aggregate of `positional_recall` over the whole document — both read the
    same tallies, so the overall score and the curve can never disagree.
    """
    tallies = _line_tallies(source, structured)
    total = sum(n for _, n in tallies)
    if not total:
        return 1.0
    return sum(hits for hits, _ in tallies) / total


def positional_recall(source: str, structured: str, *, buckets: int = 10) -> list[float]:
    """Trigram recall per equal-sized slice of the source, in document order.

    A flat curve means faithful structuring. A curve that falls as it advances
    means the model started condensing partway through — the failure a single
    overall score averages away.
    """
    tallies = _line_tallies(source, structured)
    sums = [[0, 0] for _ in range(buckets)]
    for i, (hits, total) in enumerate(tallies):
        slot = min(buckets - 1, buckets * i // len(tallies))
        sums[slot][0] += hits
        sums[slot][1] += total
    return [hits / total if total else 1.0 for hits, total in sums]


# ---------------------------------------------------------------------------
# A/B runner
# ---------------------------------------------------------------------------


def _load_fixtures(path: Path) -> list[dict]:
    return [json.loads(ln) for ln in path.read_text().splitlines() if ln.strip()]


def _load_body(queue_db: Path, fixture: dict) -> str:
    """Read one fixture's pasted article body out of queue.db.

    Bodies are not committed: they are verbatim third-party articles and this
    repo is public. The manifest pins each one by `notion_page_id` and by the
    SHA-256 of the body it was measured against, so a row that has been deleted
    or edited since fails loudly instead of silently changing the score.
    """
    with sqlite3.connect(f"file:{queue_db}?mode=ro", uri=True) as conn:
        row = conn.execute(
            "SELECT raw_content_override FROM queue_items WHERE notion_page_id = ?",
            (fixture["notion_page_id"],),
        ).fetchone()
    if row is None or not row[0]:
        raise SystemExit(f"fixture {fixture['url']} has no raw_content_override in {queue_db}")
    body = row[0]
    digest = hashlib.sha256(body.encode()).hexdigest()
    if digest != fixture["source_sha256"]:
        raise SystemExit(
            f"fixture {fixture['url']} changed since it was pinned "
            f"(expected {fixture['source_sha256'][:12]}, found {digest[:12]})"
        )
    return body


async def _score_arm(prompt: str, body: str, runs: int) -> list[float]:
    scores = []
    for _ in range(runs):
        structured, _tier, _usage = await call_cloud_chain(
            body,
            prompt,
            chain=get_chain(),
            openai_key=os.environ.get("OPENAI_API_KEY"),
            ollama_key=os.environ.get("OLLAMA_API_KEY"),
        )
        scores.append(trigram_recall(body, structured))
    return scores


def _summarise(scores: list[float]) -> str:
    mean = sum(scores) / len(scores)
    if len(scores) == 1:
        return f"{100 * mean:.1f}%"
    return f"{100 * mean:.1f}% ({100 * min(scores):.1f}-{100 * max(scores):.1f})"


async def _run(args: argparse.Namespace) -> None:
    fixtures = _load_fixtures(args.fixtures)
    candidate = args.prompt.read_text()
    baseline = args.baseline.read_text() if args.baseline else None
    print(f"chain: {[(e.provider, e.model) for e in get_chain()]}  runs: {args.runs}")
    for fixture in fixtures:
        body = _load_body(args.queue_db, fixture)
        cand = await _score_arm(candidate, body, args.runs)
        line = f"{fixture['source_chars']:>7,} chars  {args.prompt.name} {_summarise(cand)}"
        if baseline is not None:
            base = await _score_arm(baseline, body, args.runs)
            delta = 100 * (sum(cand) / len(cand) - sum(base) / len(base))
            line += f"  vs {args.baseline.name} {_summarise(base)}  ({delta:+.1f}pp)"
        print(f"{line}  {fixture['url'][:60]}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Score how much of a pasted article body survives POST /v1/structure. "
            "Run from services/fetcher with the chain's API keys in env: "
            "set -a && source .env && set +a && uv run python evals/structure_fidelity.py ..."
        )
    )
    parser.add_argument(
        "--queue-db",
        type=Path,
        required=True,
        help="Path to a queue.db holding the pinned fixtures' raw_content_override bodies.",
    )
    parser.add_argument(
        "--prompt", type=Path, default=Path("prompts/structure_v2.md"), help="Prompt to score."
    )
    parser.add_argument(
        "--baseline", type=Path, default=None, help="Second prompt to score against, for an A/B."
    )
    parser.add_argument(
        "--runs",
        type=int,
        default=3,
        help="Runs per prompt per fixture. Report the mean of at least 3, never a single run.",
    )
    parser.add_argument(
        "--fixtures", type=Path, default=Path("evals/datasets/structure_fidelity_fixtures.jsonl")
    )
    asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    main()
