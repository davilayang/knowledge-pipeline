# `prompts/extraction/`

Prompt assets consumed by `workflows.extraction`. Each file has its own header explaining what it does and how it's called — read those for the per-prompt contract. This README only documents which file plays which role.

**Header/body convention.** Each prompt file opens with a design-notes / change-history header, then a `---` horizontal rule, then the model-facing body. Everything above the first `---` is stripped at load by `domains.extraction.strip_design_notes` — applied at every read site (production `ExtractorRegistry` + the eval harness/notebooks) so what ships equals what's evaluated. Record what changed each iteration (and why) in that header; it never reaches the model. A file with no `---` is treated as body-only.

## Active in production (loaded by `ExtractorRegistry`)

`ThreeCallOpenAIExtractor` (v2) is the only production strategy. Three calls per item, one prompt each:

| Label (`def_config.py`) | File | Call |
|---|---|---|
| `PROMPT_LABEL_NARRATIVE = "narrative_v2"` | `narrative_v2.md` | 1 — free-form markdown, threads-first |
| `PROMPT_LABEL_TOPIC_CARD = "topic_card_v1"` | `topic_card_v1.md` | 2 — structured `TopicCard`, JSON mode (runs before call 3, sharing its prompt cache) |
| `PROMPT_LABEL_FOLLOWUPS = "followups_v1"` | `followups_v1.md` | 3 — structured `Followups`, JSON mode (runs after call 2, reading its prompt cache) |

Labels map to filenames as `<label>.md`. To bump a prompt, edit the file AND the label constant in the same commit.

Also active in production, but read directly via `read_extraction_prompt` rather than through `ExtractorRegistry` (it runs upstream of both extraction branches, not as one of the three calls above): `PROMPT_LABEL_METADATA = "metadata_v1"` → `metadata_v1.md`, called once per item by the `extract_metadata` asset.

## Reference / eval variants (not loaded by production)

| File | Notes |
|---|---|
| `narrative_v3.md` | **Candidate, not yet active.** Carries `narrative_v2`'s three sections plus four delivery-layer sections (speakers, structure, load-bearing subset, ordered beats) and an explicit output-language rule. Becomes active when `PROMPT_LABEL_NARRATIVE` is bumped, which waits on its coverage eval. |
| `narrative_v1.md` | Superseded by `narrative_v2.md` (fixed-section basket, replaced by threads-first coverage) — kept as reference / eval baseline. |
| `v5_article_kp_copy_2026_05_31.md` | Original single-shot v5 prompt (Article path) — kept as reference / future eval variant input. |
| `v5_arxiv_kp_copy_2026_06_01.md` | Same, arXiv path. |
| `v5_youtube_kp_copy_2026_06_01.md` | Same, YouTube path. |

The v1 trio above was derived from these v5 single-shots by splitting the schema into three calls. Keeping the v5 files lets evals A/B the single-shot baseline against the 3-call cohort without resurrecting old prompt copies from git history.

## Prompt size and the OpenAI prompt cache

A prompt that leads a request is also that request's cacheable prefix, so its
**length is a functional property, not just a style choice**. Only the
`narrative` call is affected: its messages are `[system: prompt, user: article]`,
so the prompt body is the sole text repeating across items and therefore the only
thing that can be cached. `metadata`, `topic_card` and `followups` lead with the
short `SHARED_SYSTEM` and reuse the *article* behind it instead (see
`workflows.extraction.shared_prefix`), so their leading static text is
deliberately short and no length rule applies to them.

**The documented 1024-token minimum is not the number that matters here.** 1024
is the floor when a whole request repeats. When only the leading system message
repeats and the user message differs — the narrative call's shape — caching
starts much higher. Measured 2026-08-30 against the production model
`gpt-5-mini`, sending a fresh article behind an identical system message each
time and reading `cached_tokens` on the 2nd through 4th call:

| system prompt tokens | `cached_tokens` on calls 2-4 |
|---|---|
| 897 (`narrative_v2`, active) | 0, 0, 0 |
| 1225 | 0, 0, 0 |
| 1503 | 0, 0, 0 |
| 1705 | 0, 0, 0 |
| 1904 | 1792, 1792, 1792 |
| 2044 (`narrative_v3`) | 1792, 1792, 1792 |

So the threshold sits between **1705 and 1904 tokens**. `narrative_v2` never
caches; `narrative_v3` does, with about 140 tokens to spare.
`tests/workflows/extraction/test_prompt_cache_floor.py` pins `narrative_v3`
above that line, because shrinking it back under is invisible in review.

Two related facts from the same session, worth not re-deriving:

- **`response_format` partitions the cache.** A byte-identical 3000-token prefix
  written with no `response_format` returned `cached_tokens=0` when re-sent with
  `{"type": "json_object"}`, while a no-`response_format` control on the same
  write returned 2944. This is why the narrative call cannot share the article
  prefix that `topic_card` and `followups` share — it emits markdown and they
  emit JSON, so they sit in different cache partitions no matter how the
  messages are arranged.
- **The saving is small.** 1792 cached tokens per item at `gpt-5-mini`'s 90%
  cached-input discount is roughly $0.09 per 225 items. Prompt length is worth
  getting right when a prompt is being written anyway; it is not on its own a
  reason to grow one.

## Resolution

`KP_PROMPTS_ROOT` env var → see [`../README.md`](../README.md).
