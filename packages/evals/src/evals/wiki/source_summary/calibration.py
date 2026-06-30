"""Tagging-judge calibration against a human gold set.

The producer benchmark reports judge-vs-producer agreement; that is only
trustworthy if the judge itself is right. This scores the TaggingJudge against
human-labelled `gold_tag`s: per claim, run the judge and compare its `correct_tag`
to the gold. High judge accuracy → the benchmark's tagging numbers can be
trusted; low → the judge (or the rubric) is the problem, not the producer.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from domains.wiki.source_summary import SourceClaim

from evals.core.fixtures import load_fixtures

GOLD_PATH = Path(__file__).resolve().parents[4] / "datasets" / "source_summary_tagging_gold.jsonl"
SCHEMA_VERSION = 1


@dataclass(frozen=True)
class GoldClaim:
    source_id: str
    claim_text: str
    producer_tag: str  # what the producer tagged it (shown to the judge, as in production)
    gold_tag: str  # the human-correct tag


@dataclass(frozen=True)
class Disagreement:
    source_id: str
    claim_text: str
    judge_tag: str
    gold_tag: str


@dataclass(frozen=True)
class CalibrationResult:
    n: int
    judge_accuracy: float
    disagreements: list[Disagreement]


def load_gold(path: Path = GOLD_PATH) -> list[GoldClaim]:
    _, rows = load_fixtures(path, expected_schema_version=SCHEMA_VERSION)
    return [
        GoldClaim(
            source_id=r["source_id"],
            claim_text=r["claim_text"],
            producer_tag=r["producer_tag"],
            gold_tag=r["gold_tag"],
        )
        for r in rows
    ]


def calibrate(
    gold: list[GoldClaim], source_bodies: dict[str, str], *, judge: Any
) -> CalibrationResult:
    """Run the judge over the gold claims (grouped by source, in gold order) and
    compare its `correct_tag` to the human `gold_tag`."""
    by_source: dict[str, list[GoldClaim]] = {}
    for g in gold:
        by_source.setdefault(g.source_id, []).append(g)

    correct = 0
    disagreements: list[Disagreement] = []
    for source_id, golds in by_source.items():
        claims = [
            SourceClaim(
                text=g.claim_text, source_id=source_id, speculative=g.producer_tag == "opinion"
            )
            for g in golds
        ]
        score = judge.score(claims=claims, source=source_bodies[source_id])
        for g, verdict in zip(golds, score.verdicts, strict=True):
            if verdict.correct_tag == g.gold_tag:
                correct += 1
            else:
                disagreements.append(
                    Disagreement(source_id, g.claim_text, verdict.correct_tag, g.gold_tag)
                )
    n = len(gold)
    return CalibrationResult(
        n=n, judge_accuracy=correct / n if n else 1.0, disagreements=disagreements
    )
