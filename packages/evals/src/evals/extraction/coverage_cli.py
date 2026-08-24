"""eval-narrative-coverage — re-run the narrative-coverage benchmark.

The committed surface for re-scoring narrative coverage when the narrative
prompt (or model) changes. Builds the three-call extractor with a chosen
narrative prompt, scores each candidate narrative present/absent against the
pinned gold threads (`datasets/narrative_coverage_gold.jsonl`), and reports
`coverage@present` as the **mean of N full re-runs + observed range**,
stratified by content shape. `--baseline` diffs a candidate prompt against a
prior one in the same pass (the ship gate: does the new prompt cover ≥ the old
on every shape?).

Needs `OPENAI_API_KEY` in env — extraction and the present/absent judge both
call OpenAI:

    set -a && source .env && set +a && \
      uv run eval-narrative-coverage --narrative narrative_v2 --baseline narrative_v1 --runs 3

Headline mean + range go to the Knowledge OS — Eval Runs Notion DB (see
`packages/evals/README.md` — record the mean of ≥3 runs, never a single run);
detailed per-run JSON persists under `data/eval_runs/`.
"""

import argparse
import json
import os
from collections.abc import Sequence
from pathlib import Path

from domains.extraction.prompts import strip_design_notes

from evals.core import RunManifest, load_fixtures
from evals.core.harness import run_repeated
from evals.core.manifest import code_rev
from evals.extraction import (
    ExtractionFixture,
    NarrativeCoverageScorer,
    make_three_call_variant,
)

_DEFAULT_GOLD = "packages/evals/datasets/narrative_coverage_gold.jsonl"


def _repo_root() -> Path:
    for parent in [Path.cwd(), *Path.cwd().parents]:
        if (parent / "pyproject.toml").exists() and (parent / "packages").is_dir():
            return parent
    return Path.cwd()


def _fixtures(gold_path: Path) -> tuple[list[ExtractionFixture], int]:
    """Fixtures plus the gold's `gold_version` — the data revision, which
    `load_fixtures` parks in `FixtureHeader.extra` and which is distinct from
    the schema version it validates."""
    header, rows = load_fixtures(gold_path, expected_schema_version=1)
    return [
        ExtractionFixture(
            fixture_id=r["fixture_id"],
            content_type=r["content_type"],
            content=r["content"],
            expected_topic_card={},
            content_shape=r.get("content_shape"),
            gold_threads=r["gold_threads"],
        )
        for r in rows
    ], int(header.extra.get("gold_version", 1))


def _make_judge(api_key: str, model: str):
    import openai

    client = openai.OpenAI(api_key=api_key)

    def judge(prompt: str) -> dict:
        resp = client.chat.completions.create(
            model=model,
            max_tokens=2048,
            response_format={"type": "json_object"},
            messages=[{"role": "user", "content": prompt}],
        )
        try:
            return json.loads(resp.choices[0].message.content or "{}")
        except json.JSONDecodeError:
            return {}

    return judge


def _run_arm(
    *,
    narrative_file: str,
    fixtures,
    prompts_dir: Path,
    api_key: str,
    model: str,
    max_tokens: int,
    runs: int,
    gold_rel: str,
    gold_version: int,
):
    label = narrative_file.replace(".md", "")
    variant = make_three_call_variant(
        name=label,
        narrative_prompt_text=strip_design_notes((prompts_dir / narrative_file).read_text()),
        topic_card_prompt_text=strip_design_notes((prompts_dir / "topic_card_v1.md").read_text()),
        followups_prompt_text=strip_design_notes((prompts_dir / "followups_v1.md").read_text()),
        prompt_versions={
            "narrative": label,
            "topic_card": "v1",
            "followups": "v1",
        },
        model=model,
        api_key=api_key,
        max_tokens=max_tokens,
        code_revision=code_rev(),
    )
    scorer = NarrativeCoverageScorer(chat_fn=_make_judge(api_key, model))
    manifest = RunManifest(
        dataset=Path(gold_rel).name,
        dataset_schema=1,
        dataset_version=gold_version,
        subject=label,
        subject_model=model,
        judge_model=model,
        code_rev=code_rev(),
        mode="report",
        runs=runs,
    )
    report = run_repeated(
        variant=variant,
        fixtures=fixtures,
        scorer=scorer,
        manifest=manifest,
        runs=runs,
        target="extraction",
        fixture_set=gold_rel,
    )
    return {
        "variant": label,
        "mean": report.mean,
        "lo": report.lo,
        "hi": report.hi,
        "per_run": report.per_run,
        "by_shape": report.by_stratum.get("by_content_shape", {}),
        "by_type": report.by_stratum.get("by_content_type", {}),
    }


def _print_arm(a: dict, runs: int) -> None:
    print(f"\n===== {a['variant']} — mean of {runs} run(s) =====")
    print(f"aggregate coverage@present = {a['mean']:.3f}  (range {a['lo']:.3f}–{a['hi']:.3f})")
    for label, cells in (("content_type", a["by_type"]), ("content_shape", a["by_shape"])):
        print(f"  per {label}:")
        for stratum, v in sorted(cells.items()):
            print(f"    {stratum:12s} {v:.3f}")


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="eval-narrative-coverage")
    p.add_argument("--narrative", default="narrative_v2", help="candidate narrative prompt label")
    p.add_argument("--baseline", default=None, help="optional prior prompt label to diff against")
    p.add_argument(
        "--runs", type=int, default=3, help="full re-runs to average (LLM judge is noisy)"
    )
    p.add_argument(
        "--fixtures", type=Path, default=None, help=f"gold JSONL (default {_DEFAULT_GOLD})"
    )
    p.add_argument("--model", default="gpt-4.1-mini")
    p.add_argument("--max-tokens", type=int, default=4096)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)

    root = _repo_root()
    gold_rel = str(args.fixtures) if args.fixtures else _DEFAULT_GOLD
    gold_path = args.fixtures if args.fixtures else root / _DEFAULT_GOLD
    fixtures, gold_version = _fixtures(gold_path)
    total_chars = sum(len(f.content) for f in fixtures)
    n_arms = 2 if args.baseline else 1
    print(
        f"gold: {len(fixtures)} fixtures, {total_chars:,} content chars, "
        f"{sum(len(f.gold_threads or []) for f in fixtures)} threads | "
        f"{n_arms} arm(s) × {args.runs} run(s), model={args.model}"
    )

    if args.dry_run:
        approx_in = total_chars / 4  # chars→tokens
        # each run: 3 extraction calls + 1 judge call per fixture, gpt-4.1-mini ~$0.40/1M in
        est = n_arms * args.runs * approx_in * 4 * 0.4 / 1e6
        print(f"DRY RUN — est ≈ ${est:.2f}")
        return 0

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("OPENAI_API_KEY not set — `set -a && source .env && set +a` first.")
        return 2

    prompts_dir = root / "prompts" / "extraction"
    common = dict(
        fixtures=fixtures,
        prompts_dir=prompts_dir,
        api_key=api_key,
        model=args.model,
        max_tokens=args.max_tokens,
        runs=args.runs,
        gold_rel=gold_rel,
        gold_version=gold_version,
    )
    arms = []
    if args.baseline:
        arms.append(_run_arm(narrative_file=f"{args.baseline}.md", **common))
    arms.append(_run_arm(narrative_file=f"{args.narrative}.md", **common))
    for a in arms:
        _print_arm(a, args.runs)

    if len(arms) == 2:
        base, cand = arms
        print(f"\n===== Δ ({cand['variant']} − {base['variant']}) =====")
        print(f"aggregate: {cand['mean'] - base['mean']:+.3f}")
        for key, label_ in (("by_type", "content_type"), ("by_shape", "content_shape")):
            print(f"  per {label_}:")
            for stratum in sorted(set(base[key]) | set(cand[key])):
                d = cand[key].get(stratum, 0.0) - base[key].get(stratum, 0.0)
                flag = "" if d >= -1e-9 else "  ← REGRESSION"
                print(f"    {stratum:12s} {d:+.3f}{flag}")

    print("\n===== Notion Eval Runs rows (log the mean; see packages/evals/README.md) =====")
    dataset = Path(gold_rel).name
    for a in arms:
        shape_str = ", ".join(f"{s}={v:.2f}" for s, v in sorted(a["by_shape"].items()))
        type_str = ", ".join(f"{s}={v:.2f}" for s, v in sorted(a["by_type"].items()))
        value = f"{a['mean']:.3f} (range {a['lo']:.3f}-{a['hi']:.3f})"
        row = (
            f"Project=knowledge-pipeline | Benchmark=extraction-coverage | "
            f"Variant={a['variant']} | Metric=coverage@present | Value={value} | "
            f"N={len(fixtures)} | Dataset={dataset}@v{gold_version} | Model={args.model} | "
            f"per-type: {type_str} | per-shape: {shape_str}"
        )
        print(f"  {row}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
