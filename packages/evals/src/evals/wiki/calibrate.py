"""Run the wiki judges on a calibration JSONL and print per-page breakdowns —
the human-calibration gate (compare the judge's verdict against your own read).

    uv run python -m evals.wiki.calibrate data/eval_calibration/wiki_quality_calib.jsonl
"""

import json
import os
import pathlib
import sys

from workflows.costs import cost_usd
from workflows.llm import LLMCall

from evals.wiki.chat import make_faithfulness_chat_fn, make_specificity_chat_fn
from evals.wiki.judges import FaithfulnessJudge, SpecificityJudge


def _load_key() -> None:
    if "OPENAI_API_KEY" in os.environ:
        return
    env = pathlib.Path(".env")
    if env.exists():
        for line in env.read_text().splitlines():
            if line.startswith("OPENAI_API_KEY="):
                os.environ["OPENAI_API_KEY"] = line.split("=", 1)[1].strip().strip("\"'")
                return


def main(path: str) -> None:
    _load_key()
    calls: list[LLMCall] = []
    fj = FaithfulnessJudge(chat_fn=make_faithfulness_chat_fn(calls_sink=calls))
    sj = SpecificityJudge(chat_fn=make_specificity_chat_fn(calls_sink=calls))

    for line in pathlib.Path(path).read_text().splitlines():
        r = json.loads(line)
        page = r["page_md"]
        sources = [s["text"] for s in r["sources"]]

        f = fj.score(page=page, sources=sources)
        s = sj.score(entity=r["canonical_name"], page=page, sources=sources)

        print(f"\n=== {r['canonical_name']} ({r['page_type']}, {r['n_sources']} src) ===")
        kept = len(f.claims) - f.unsupported_count
        print(
            f"FAITHFULNESS  grounded {f.grounded_fraction:.2f}  "
            f"({kept}/{len(f.claims)} claims, {f.unsupported_count} unsupported)"
        )
        for c in f.claims:
            if not c.supported:
                print(f"    ✗ {c.text}")
        print(
            f"SPECIFICITY   nums/dates {s.numbers_dates_recall:.2f} | "
            f"names/orgs {s.names_orgs_recall:.2f} | quotes {s.quote_recall:.2f} | "
            f"abstraction_penalty {s.abstraction_penalty}"
        )
        for ab in s.metadata["raw"].get("abstractions", []):
            print(
                f"    abstraction: {ab.get('source_specific')!r} → {ab.get('page_placeholder')!r}"
            )

    tin = sum(c.input_tokens for c in calls)
    tout = sum(c.output_tokens for c in calls)
    usd = sum((cost_usd(c.model, c.input_tokens, c.output_tokens) for c in calls), 0.0)
    print(f"\n--- {len(calls)} judge calls | {tin} in + {tout} out tokens | ~${usd:.3f} ---")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "data/eval_calibration/wiki_quality_calib.jsonl")
