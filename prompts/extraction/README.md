# `prompts/extraction/`

> **Cross-repo contract.** The `narrative` output is consumed by
> `newsletter-assistant`'s voice agent, whole and unparsed — the section headers
> are the entire interface between the two repos. Before changing them, read
> [`knowledge-os/contracts/narrative.md`](https://github.com/davilayang/data-context-builder/blob/main/documents/knowledge-os/contracts/narrative.md) in the shared hub repo `davilayang/data-context-builder`
> (locally: `~/GitHub/data-context-builder/documents/knowledge-os/contracts/narrative.md`).
> It carries the section shape, the completeness guarantees, and both sides'
> obligations.

Prompt assets consumed by the standalone `services/fetcher` process, behind `POST /v1/extract`. Each file has its own header explaining what it does and how it's called — read those for the per-prompt contract. This README only documents which file plays which role.

**Header/body convention.** Each prompt file opens with a design-notes / change-history header, then a `---` horizontal rule, then the model-facing body. Everything above the first `---` is stripped at load by `domains.extraction.prompts.strip_design_notes` — applied at every read site (the fetcher service's `load_prompt` + the eval harness/notebooks) so what ships equals what's evaluated. Record what changed each iteration (and why) in that header; it never reaches the model. A file with no `---` is treated as body-only.

## Active in production (loaded by the fetcher's `/v1/extract`)

Four tasks, one prompt each, declared as `TaskSpec` entries in the fetcher's `extract/tasks.py`:

| Task | Default prompt label | File |
|---|---|---|
| `metadata` | `metadata_v1` | `metadata_v1.md` — structured `MetadataPayload`, JSON mode |
| `narrative` | `narrative_v3` | `narrative_v3.md` — structured `Narrative`, JSON mode (writes the shared article prefix the rest read) |
| `topic_card` | `topic_card_v1` | `topic_card_v1.md` — structured `TopicCard`, JSON mode |
| `followups` | `followups_v1` | `followups_v1.md` — structured `Followups`, JSON mode |

Labels map to filenames as `<label>.md`. `tasks.py`'s declaration order (`metadata` first) is the execution order the service runs requested tasks in — knowledge-pipeline gates on `metadata` before spending the other three. To bump a prompt, edit the file AND the `default_prompt_label` on that task's `TaskSpec` in the same commit.

Whichever tasks a caller requests run in that fixed sequence rather than concurrently, so each reads the article from the prompt cache the one before it wrote — see the cache section below for why the order and the message shape are load-bearing.

**The narrative's section headers are not in its prompt file.** They come from the `title` on each field of `domains.extraction.schemas.Narrative`, and `domains.extraction.render.render_narrative` emits them when turning the stored json back into the text the voice agent reads. Renaming a section means editing the model.

`metadata` runs as its own request — knowledge-pipeline's `extract_metadata` asset calls `/v1/extract` with `tasks=["metadata"]` upstream of both extraction branches, before either commits to `narrative`/`topic_card`/`followups` (requested together as `extract_reading_card`'s one request).

## Reference / eval variants (not loaded by production)

| File | Notes |
|---|---|
| `narrative_v2.md` | Superseded by `narrative_v3.md` and **no longer runnable** — its field list came from `Narrative`, which now carries v3's sections. Kept as the record of the threads-first shape. |
| `narrative_v1.md` | Superseded by `narrative_v2.md` (fixed-section basket, replaced by threads-first coverage). Kept as a record; like v2 it can no longer be run, since the extractor generates the field list from the current `Narrative`. |
| `v5_article_kp_copy_2026_05_31.md` | Original single-shot v5 prompt (Article path) — kept as reference / future eval variant input. |
| `v5_arxiv_kp_copy_2026_06_01.md` | Same, arXiv path. |
| `v5_youtube_kp_copy_2026_06_01.md` | Same, YouTube path. |

The v1 trio above was derived from these v5 single-shots by splitting the schema into three calls. Keeping the v5 files lets evals A/B the single-shot baseline against the 3-call cohort without resurrecting old prompt copies from git history.

## Prompt size, message shape, and the OpenAI prompt cache

All four extraction calls send `[SHARED_SYSTEM, article, task]` via
`fetcher.extract.openai_lane.structured_messages` with the same `{"type": "json_object"}`
response format. Both halves are load-bearing and neither is a style choice:

- **OpenAI matches a cached prefix front-to-back from position zero**, so the
  article has to sit ahead of anything that differs per call, and every per-call
  difference — the role prompt, the reader-notes fold, the generated schema, a
  retry correction — belongs in the trailing task message.
- **OpenAI partitions the prefix cache by `response_format`.** Measured
  2026-08-30 on `gpt-5-mini`: a byte-identical 3000-token prefix written with no
  `response_format` reported `cached_tokens=0` when re-sent with
  `{"type": "json_object"}`, while a no-`response_format` control on the same
  write reported 2944. This is why all four calls send `json_object` rather than
  `json_schema` — one shared value keeps four different pydantic models in one
  partition, with each model's schema travelling in its task tail.

The narrative call broke both rules until 2026-08-31: it led with its own prompt
file rather than `SHARED_SYSTEM` (the two are byte-identical for 145 bytes, then
diverge on a pair of markdown emphasis asterisks) and it sent no
`response_format` because it returned markdown. It therefore sat in a partition
of one, and the article was a cache miss on the narrative and a cache write on
the topic card — **billed twice per item**. It now returns one field per section
and is rendered back to text by `domains.extraction.render`.

Measured on a 5,028-token article, one item end to end:

| call | tokens in | cached |
|---|---|---|
| `narrative` | 6,325 | 0 — writes the shared prefix |
| `topic_card` | 7,367 | 4,864 |
| `followups` | 6,285 | 4,864 |

`topic_card` reported 0 before the change, because it was the writer.

**A prompt's length no longer affects the cache**, because no prompt leads a
request any more — they all ride in the task tail. Do not size a prompt with the
cache in mind.

**Short sources do not cache, and that is expected.** OpenAI only reuses a
prefix once it is long enough, and the threshold is higher when only part of the
request repeats than when the whole request does. Measured on `gpt-5-mini` by
sending a fresh user message behind an identical leading prefix: 1,705 tokens
cached nothing, 1,904 cached 1,792. The shared prefix here is `SHARED_SYSTEM`
(105 tokens) plus the article, so an article under roughly 1,800 tokens caches
nothing on any call. The same 5,028-token article above caches; a 1,257-token
one reported 0 across all three.

## Resolution

`FETCHER_EXTRACTION_PROMPTS_ROOT` env var, read by `services/fetcher` — see [`../README.md`](../README.md).
