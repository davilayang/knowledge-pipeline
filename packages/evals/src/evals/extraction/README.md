# `evals/extraction/`

First per-pipeline harness consuming `evals/core/`. Wraps `workflows.extraction.ThreeCallOpenAIExtractor` into a `Variant` so prompt swaps + content transforms can be A/B tested in a notebook or scored over a fixture set via the `eval-extraction` CLI.

## Public API

See `evals/extraction/__init__.py` for the re-export list.

## Scorer mix

| Topic Card field | Scorer |
|---|---|
| `extracted_title` | ExactMatchJudge |
| `core_mechanism`, `transferable_pattern` | EmbeddingSimilarityJudge (OpenAI text-embedding-3-small by default) |
| `best_example`, `main_tension`, `candidate_tie_backs` | LLMJudge (gpt-4o-2024-11-20 by default) |

Scorers carry no provider dependency — callers inject `embed_fn` and `chat_fn`. Tests pass stubs; runtime callers wire OpenAI clients.

## Scope notes

- Fixtures pair raw content with a **v5 baseline expected output** (production extractor as of 2026-06-06). The baseline is a **regression anchor, not human-curated gold**.
- **Measurement floor:** the scorer surfaces *change vs v5*, not *absolute quality*. A +0.03 overall bump tells you a variant produces v5-shaped output better; it does NOT tell you the variant is closer to ground truth. Improvements along dimensions v5 already optimised (brevity in `extracted_title`, narrative density in `core_mechanism`) are detectable; improvements along dimensions v5 *doesn't* optimise are systematically under-credited. Treat scores as direction signals, not absolute rankings, until human-curated gold lands post-Step 6.
- First cut: 5 fixtures × 3 content types = 15. Refresh to 15/type after `eval-corpus` tooling lands (spec Step 6).
