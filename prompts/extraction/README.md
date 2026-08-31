# `prompts/extraction/`

> **Cross-repo contract.** The `narrative` output is consumed by
> `newsletter-assistant`'s voice agent, whole and unparsed — the section headers
> are the entire interface between the two repos. Before changing them, read
> [`knowledge-os/contracts/narrative.md`](https://github.com/davilayang/data-context-builder/blob/main/documents/knowledge-os/contracts/narrative.md) in the shared hub repo `davilayang/data-context-builder`
> (locally: `~/GitHub/data-context-builder/documents/knowledge-os/contracts/narrative.md`).
> It carries the section shape, the completeness guarantees, and both sides'
> obligations.

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

## Resolution

`KP_PROMPTS_ROOT` env var → see [`../README.md`](../README.md).
