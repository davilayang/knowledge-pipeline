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
from collections import Counter
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from fetcher.extractors import transcript_structurer
from fetcher.extractors._cloud_chain import ChainEntry, call_cloud_chain
from fetcher.extractors.structure import get_chain
from fetcher.extractors.youtube_transcript import chunks_to_markdown


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
    remaining = Counter(_trigrams(structured))
    tallies = []
    for line in source.split("\n"):
        trigrams = _trigrams(line)
        if not trigrams:
            continue
        hits = 0
        for trigram in trigrams:
            # Each output occurrence is consumed by at most one source
            # occurrence. Without this, an output keeping one of five identical
            # code blocks would satisfy all five and score a perfect 1.0 --
            # blind to the "kept the first of several, dropped the rest"
            # failure the structurer prompt exists to prevent.
            if remaining[trigram] > 0:
                remaining[trigram] -= 1
                hits += 1
        tallies.append((hits, len(trigrams)))
    return tallies


def trigram_recall(source: str, structured: str) -> float:
    """Share of the source's word-trigrams that survive into the structured text."""
    tallies = _line_tallies(source, structured)
    total = sum(n for _, n in tallies)
    if not total:
        return 1.0
    return sum(hits for hits, _ in tallies) / total


# ---------------------------------------------------------------------------
# A/B runner
# ---------------------------------------------------------------------------


def _load_fixtures(path: Path) -> list[dict]:
    return [json.loads(ln) for ln in path.read_text().splitlines() if ln.strip()]


def _verify_pin(body: str, fixture: dict) -> str:
    digest = hashlib.sha256(body.encode()).hexdigest()
    if digest != fixture["source_sha256"]:
        raise SystemExit(
            f"fixture {fixture['url']} changed since it was pinned "
            f"(expected {fixture['source_sha256'][:12]}, found {digest[:12]})"
        )
    return body


def _load_article_body(queue_db: Path, fixture: dict) -> str:
    """Read a pasted article body out of queue.db.

    Bodies are not committed: they are verbatim third-party articles and this
    repo is public. The manifest pins each one by `notion_page_id` and by the
    SHA-256 of the body it was measured against, so a row that has been deleted
    or edited since fails loudly instead of silently changing the score.
    """
    with closing(sqlite3.connect(f"file:{queue_db}?mode=ro", uri=True)) as conn:
        row = conn.execute(
            "SELECT raw_content_override FROM queue_items WHERE notion_page_id = ?",
            (fixture["notion_page_id"],),
        ).fetchone()
    if row is None or not row[0]:
        raise SystemExit(f"fixture {fixture['url']} has no raw_content_override in {queue_db}")
    return _verify_pin(row[0], fixture)


def _load_transcript_body(fetches_db: Path | None, fixture: dict) -> str:
    """Rebuild a transcript structurer input from the caption chunks in fetches.db.

    The handler feeds `chunks_to_markdown(chunks)` to the structurer and stores
    only the structured result, so the input is reconstructed the same way here
    rather than read back. Note this fixture is less durable than the article
    ones: cache rows carry a TTL and are deleted on the first lookup after they
    expire, where `queue_items` rows never expire.
    """
    if fetches_db is None:
        raise SystemExit(
            f"fixture {fixture['url']} is a transcript fixture; pass --fetches-db to load it"
        )
    with closing(sqlite3.connect(f"file:{fetches_db}?mode=ro", uri=True)) as conn:
        row = conn.execute(
            "SELECT metadata_json FROM cache WHERE url_hash = ?", (fixture["url_hash"],)
        ).fetchone()
    if row is None:
        raise SystemExit(
            f"fixture {fixture['url']} is not in {fetches_db} — the cache row has expired "
            f"or been evicted, so this fixture can no longer be run"
        )
    chunks = json.loads(row[0]).get("chunks")
    if not chunks:
        raise SystemExit(f"fixture {fixture['url']} cache row carries no caption chunks")
    return _verify_pin(chunks_to_markdown(chunks), fixture)


@dataclass(frozen=True)
class RunResult:
    """One structuring run: its score, a digest of the exact output, and which
    chain entry served it.

    The digest and the tier are what let a stability claim be checked. Equal
    scores do not imply equal outputs — trigram recall is position-blind set
    membership, so reordered or re-emphasised text scores the same. And an
    Ollama timeout falling through to the OpenAI entry would otherwise be
    invisible, silently making the two arms a model comparison rather than a
    prompt comparison.
    """

    recall: float
    digest: str
    tier: str
    finish_reason: str | None


async def _score_arm(prompt: str, body: str, runs: int, chain: list[ChainEntry]) -> list[RunResult]:
    results = []
    for _ in range(runs):
        structured, tier, usage = await call_cloud_chain(
            body,
            prompt,
            chain=chain,
            openai_key=os.environ.get("OPENAI_API_KEY"),
            ollama_key=os.environ.get("OLLAMA_API_KEY"),
        )
        results.append(
            RunResult(
                recall=trigram_recall(body, structured),
                digest=hashlib.sha256(structured.encode()).hexdigest()[:12],
                tier=tier,
                finish_reason=usage.get("finish_reason"),
            )
        )
    return results


def _mean_recall(results: list[RunResult]) -> float:
    return sum(r.recall for r in results) / len(results)


def _summarise(results: list[RunResult]) -> str:
    """Render an arm as mean, range, and how many distinct outputs produced it."""
    mean = 100 * _mean_recall(results)
    if len(results) == 1:
        return f"{mean:.1f}%"
    lo = 100 * min(r.recall for r in results)
    hi = 100 * max(r.recall for r in results)
    distinct = len({r.digest for r in results})
    return f"{mean:.1f}% ({lo:.1f}-{hi:.1f}, {distinct}/{len(results)} distinct)"


async def _run(args: argparse.Namespace) -> None:
    fixtures = _load_fixtures(args.fixtures)
    candidate = args.prompt.read_text()
    baseline = args.baseline.read_text() if args.baseline else None
    print(f"chain: {[(e.provider, e.model) for e in get_chain()]}  runs: {args.runs}")
    for fixture in fixtures:
        if fixture.get("lane") == "transcript":
            print(await _run_transcript_guard(args, fixture), flush=True)
            continue
        print(await _run_article_ab(args, fixture, candidate, baseline), flush=True)


async def _run_transcript_guard(args: argparse.Namespace, fixture: dict) -> str:
    """Regression guard for the transcript structurer, which this repo does not
    change.

    It shows a single call *can* hold fidelity at 100k+ characters. It does not
    show that one reliably does: a review from `newsletter-assistant` recorded
    the same endpoint collapsing a ~90k transcript to 15-18% of its length
    twice, and chunking that same input to ~12k windows recovered 98%. Four
    fresh runs here at 91k and 99k retained 87-96%, so the behaviour is
    bimodal and the trigger is not yet identified. Treat this fixture as a
    floor alarm on one known-good input, not as evidence that length is safe.
    """
    body = _load_transcript_body(args.fetches_db, fixture)
    results = await _score_arm(
        transcript_structurer.get_prompt(), body, args.runs, transcript_structurer.get_chain()
    )
    mean = _mean_recall(results)
    floor = fixture["recall_floor"]
    verdict = "ok" if mean >= floor else f"!! BELOW FLOOR {100 * floor:.1f}%"
    line = (
        f"{fixture['source_chars']:>7,} chars  [transcript guard] "
        f"{_summarise(results)} vs floor {100 * floor:.1f}%  {verdict}"
    )
    return _annotate(line, results) + f"  {fixture['url'][:60]}"


async def _run_article_ab(
    args: argparse.Namespace, fixture: dict, candidate: str, baseline: str | None
) -> str:
    body = _load_article_body(args.queue_db, fixture)
    chain = get_chain()
    cand = await _score_arm(candidate, body, args.runs, chain)
    base: list[RunResult] = []
    line = f"{fixture['source_chars']:>7,} chars  {args.prompt.name} {_summarise(cand)}"
    if baseline is not None:
        base = await _score_arm(baseline, body, args.runs, chain)
        delta = 100 * (_mean_recall(cand) - _mean_recall(base))
        line += f"  vs {args.baseline.name} {_summarise(base)}  ({delta:+.1f}pp)"
    return _annotate(line, cand + base) + f"  {fixture['url'][:60]}"


def _annotate(line: str, results: list[RunResult]) -> str:
    """Append the two flags that decide whether a number can be trusted."""
    served = {r.tier for r in results}
    # More than one chain entry served this fixture, so the arms differ by model
    # as well as by prompt and the delta does not isolate the prompt.
    if len(served) > 1:
        line += f"  !! MIXED TIERS {sorted(served)}"
    # "length" means the model hit its output cap, so the tail was cut off
    # rather than condensed by choice. No prompt wording fixes that -- it calls
    # for chunking the input instead.
    truncated = sum(1 for r in results if r.finish_reason == "length")
    if truncated:
        line += f"  !! TRUNCATED {truncated} run(s) hit the output cap"
    return line


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
        "--fetches-db",
        type=Path,
        default=None,
        help=(
            "Path to a fetches.db, needed only for transcript-lane fixtures, whose "
            "input is rebuilt from the caption chunks in the cache row."
        ),
    )
    parser.add_argument(
        "--fixtures", type=Path, default=Path("evals/datasets/structure_fidelity_fixtures.jsonl")
    )
    asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    main()
