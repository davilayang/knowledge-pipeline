# Queue comments as a captured extraction artifact (Layer 1)

**Status:** locked design (2026-06-28). Evidence-backed by spikes; architecture
boundary validated by codex second opinion.

## Problem

A reader's intent about a queued item — a focus ("the chunking section"), an
open-loop ("remind me to compare with dbt"), or ad-hoc context — exists at
*save-time* but currently has nowhere to live. By the time the item resurfaces
in a session (days later), that intent has evaporated. This is a **time-gap /
continuity problem**: carry save-time intent forward so it's honored without the
reader re-remembering it.

## Decision

**Capture, don't steer.** User comments are captured verbatim and surfaced as a
separate, labeled `reader_threads` artifact. They do **not** bias the canonical
source-grounded extraction.

Rejected alternatives (on evidence):
- **Steer the extraction** (inject the comment into narrative/topic_card to
  re-orient emphasis): empirically corrupts faithfulness. Redirecting toward a
  non-dominant section made the model fabricate to fill the gap — gpt-4o-mini
  faithfulness 5→2 (invented "data governance frameworks" absent from source),
  gpt-4o 4→3. An explicit "never invent" instruction did not hold, worst on the
  dev model.
- **Multi-agent (planner/critic) in KP**: does not address the root cause — the
  missing object is *evidence in the source*, not reasoning depth. A critic only
  makes steer "fail gracefully" (abstain when source is thin), which is coverage
  gating, not multi-agent extraction. Over-engineering here.
- Angled reads belong to **NA session-time** as a separate grounded angle-pass
  (retrieve → coverage-classify → generate from cited spans → abstain when
  thin). Out of scope for this spec; KP supports it via vectors/chunks +
  `reader_threads`, never by mutating canonical rows.

## Scope

In scope:
- Read Notion page-level comments for a queued item.
- Store them verbatim in `queue.db` (not Notion — input-only privacy stance).
- Surface them as a structured `reader_threads` field on the followups call,
  populated only when comments exist.

Out of scope:
- Steering narrative/topic_card; multi-agent extraction.
- Standing user profile (Layer 2 — KP consumes NA's `facts` table; parked).
- The NA grounded angle-pass (parked).
- Writing any extraction output back to Notion.

## Data model

- New column `queue_items.user_comments_json` (TEXT, nullable) — verbatim
  comments as JSON: `[{author, text, created_at}, ...]`. **Cohort-scoped**:
  wiped + rewritten by `upsert_triaged` on every re-triage, exactly like
  `content_type` / `content_shape`.
- `Followups` schema gains `reader_threads: list[str]` (default `[]`). Single
  schema — no conditional swap. The field defaults empty; it is only populated
  when the reader-notes block + fold instruction are present.

## Flow

1. **User**: adds URL to the Notion Queue; optionally leaves Notion comment(s)
   (free text — focus, open-loop, or context).
2. **Sensor**: unchanged. Fires on Status=Queued rows.
3. **Triage (`triaged` asset)** — *new*: calls `NotionQueueResource.get_page_comments(page_id)`,
   serializes to `user_comments_json`, persists via `upsert_triaged`. (Read here,
   not in the sensor — the sensor runs every tick over all queued rows; the asset
   runs once per partition, so the extra Notion call is cheap there.)
4. **Fetch (`fetched`)**: unchanged. On re-queue, `raw_content` was wiped by
   triage → re-fetches fresh.
5. **Extract (`extracted`)**: reads `user_comments_json` off the row, passes it
   to the extractor via a new `user_notes: str | None` param.
   - **Narrative call**: unchanged (no notes — stays cache-friendly).
   - **Topic-card call**: unchanged.
   - **Followups call**: when `user_notes` is set, the comment is appended to the
     user message as a labeled `[reader's notes — NOT part of the source]` block,
     and a fold instruction is appended to the system prompt directing the model
     to populate `reader_threads` from that block only. When `user_notes` is
     None/empty, the call is byte-identical to today (`reader_threads` returns
     `[]`).
6. **Record**: followups call output JSON now carries `reader_threads`. No new
   storage — it rides in the existing `extraction_calls` followups row. On
   re-queue the prior cohort's `extraction_calls` were deleted by triage, so this
   is a **clean replacement**, not an append.

## Re-trigger / UX contract

Notion comments do **not** bump `last_edited_time`, so adding a comment alone
does not re-fire the sensor. To apply a comment added after processing:

> **Add comment → flip Status to Queued.** This re-triages (wipes the cohort
> incl. `extraction_calls` and re-reads comments), re-fetches, re-extracts with
> the comment, and cleanly replaces the prior result.

Document this in the Queue DB description.

## Guarantees (from spike evidence)

- **Model-agnostic**: works on `gpt-4o-mini` (dev) and `gpt-4o` (prod) — the
  structured field is the lever; soft instructions / note-reordering both failed
  on mini.
- **No quality degradation**: source-grounded `topic_card` / followups questions
  judged 5/5 faithfulness + specificity with and without a comment.
- **No hallucination**: folded `reader_threads` is `[]` when no comment present
  (the dedicated-call variant hallucinated; folding into followups does not).
- **Provenance clean**: `reader_threads` is a separate labeled field, populated
  only from the notes block, never blended into source claims.

## Validation

- **TDD on code boundaries**: `get_page_comments`; `user_comments_json` column +
  idempotent migration + wipe-on-re-triage; `user_notes` param threading;
  no-comment path returns empty `reader_threads`.
- **Empirical eval (not TDD)** on prompt/field behaviour: source-faithfulness
  judge on narrative/topic_card/followups-questions (must hold vs baseline) +
  a **separate faithful-to-notes judge** on `reader_threads` (reflects only the
  notes, no source fabrication — the source-rubric mis-scores threads, as the
  spike exposed).

## Files touched

- `packages/orchestrators/.../shared/queue_resources.py` — `NotionQueueResource.get_page_comments`.
- `packages/orchestrators/.../triage_knowledge_queue/assets.py` — read + persist comments (+ `TriageInput`).
- `packages/domains/src/domains/queue_store/sources.py` — column, migration, `upsert_triaged` wipe.
- `packages/domains/src/domains/extraction/schemas.py` — `Followups.reader_threads`.
- `packages/workflows/src/workflows/extraction/protocol.py` — `user_notes` param.
- `packages/workflows/src/workflows/extraction/three_call_openai.py` — conditional followups block + fold instruction (built in code, prompt `.md` unchanged).
- `packages/orchestrators/.../fetch_extract_queue/assets.py` — pass `user_notes`.

## Open implementation decisions

1. **DAG versions**: **Resolved — do not bump.** No-comment output is
   byte-identical, so bumping `code_version` would needlessly mark all prior
   materializations stale.
2. **Provenance of the augmented prompt**: **Resolved — option B.** Compute the
   per-call `prompt_sha256` over the *actual* system prompt that ran (base
   `.md` for the no-comment path → byte-identical to today; `base + fold`
   instruction for the comment path → distinct hash, so future edits to the fold
   instruction correctly flag comment-bearing rows stale). Keep this **per-call
   only**: the cohort-level `extractor_sha256` on `queue_items` (model + three
   base prompts) stays **base-only**, because reader-threads activation is a
   per-item runtime condition, not a cohort/deploy property.
