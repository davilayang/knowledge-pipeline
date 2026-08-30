# Changelog

All notable changes to this project will be documented in this file.

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)

## [Unreleased]

### Added

- **The pipeline now captures who made a piece, who published it, how it is put together, and what its fetch lost — but nothing reads it yet, by design.** A new `extract_metadata` asset reads the fetched body once and writes `queue_items.contributors_json` / `publisher` / `delivery_json`, plus a `metadata` row in the `extraction_calls` ledger. The payoff is a few weeks of production data: whether the two-value `delivery_shape` fires at the expected ~6%, and how often a YouTube talk's substance is only on screen. Consumers are designed against that sample rather than against a 51-item hand-curated one.
  - `contributors` is multi-valued and separate from `publisher` — one guest post carries a platform byline, a real author with an affiliation, and a publishing newsletter that is none of them. `author` keeps its current meaning (the byline as the source platform reports it) and is untouched, so no existing row changes meaning.
  - The asset is **best-effort and always materialises**: a refusal, an unusable reply, a truncated reply or a dead socket writes nothing and logs why. Both extract branches now depend on it, and gating them on one metadata failure would be a blast radius neither has today. A non-blocking asset check turns red when a row with a body ends up with empty columns.
  - It sits upstream of the `extract_reading_card` / `extract_claims` fork because those are parallel siblings — anything produced inside one is permanently invisible to the other, so a field either might route on cannot be emitted by one of them.
  - Re-running an unchanged row costs nothing; a re-fetched body, a re-enrichment, an edited prompt or a model swap all re-extract.
  - **`get_queue_extraction` gains `contributors` / `publisher` / `delivery`.** Additive keys on the cross-repo read path newsletter-assistant consumes, so the new fields are reachable through the supported API rather than only by reading the table. The view stays gated on `extracted_at`, which the metadata asset does not set — metadata for an item whose reading-card extraction failed is stored but not visible here.

---

## [0.36.17] — 2026-08-30

### Changed

- **Triage keeps the page metadata it was already fetching and discarding.** The article path parsed a byline, date, site name, keywords and `og:type` off every page and kept only title + description; the arXiv path walked past the paper's authors and publication date. All now land in `enrichment_json`, with no new requests.

- **arXiv enrichment returns data again.** `export.arxiv.org` 301s http to https; unfollowed, the empty redirect body parsed as an empty feed, so every paper enriched to nothing — invisibly, since empty is also a valid result.

- **Dates are validated on capture.** A timestamp is stored as its `YYYY-MM-DD` day and an unparseable value is dropped, so consumers can call `date.fromisoformat` on the field directly.

- **The `content_shape` classifier's prompt is pinned to the fields it already read.** It builds from whatever enrichment holds, so the newly kept fields would have silently entered it — and `content_shape` selects the extraction prompt bundle.

---

## [0.36.16] — 2026-08-29

### Changed

- **A narrative cut short at the model's completion limit now fails the item instead of being stored as if whole.** `_narrative_call` checks `finish_reason == "length"`, as the structured calls already did; the failed Notion row explains the limit.

---

## [0.36.15] — 2026-08-28

### Changed

- **An empty narrative from OpenAI is now retried once instead of failing the item.** The call intermittently returns an empty completion — `finish_reason="stop"`, no refusal — and the whole extraction failed with it, discarding the topic card and follow-ups that had already succeeded.

- **The failure message on a Notion queue row now explains what happened.** It read `String should have at least 1 character` for `narrative_md`; it now names the empty completion, the item, and how to re-queue. Refusals are reported as refusals rather than misread as empty completions.

- **A whitespace-only narrative is treated as empty.** `"   "` cleared both the emptiness check and the schema's `min_length=1`, so an item could be stored carrying a blank narrative.

---

## [0.36.14] — 2026-08-28

### Changed

- **Extraction's `topic_card` and `followups` calls now share the article via OpenAI's prompt cache instead of paying for it twice.** They moved from pydantic Structured Outputs to sequential JSON-mode calls; cache reuse measured 53–98% of `followups`' input on real articles, and 0% below ~4,000 characters where the shared prefix falls under OpenAI's 1,024-token minimum. Adds ~4s/item to nightly batches. (`workflows.extraction.three_call_openai`, `shared_prefix.py`)

- **The JSON schema sent to the model is now generated from the pydantic model, not written into prompt markdown.** `domains.extraction.schemas` stays the single source of truth for `topic_card_v1` and `followups_v1`.

- **A malformed structured-call reply is now retried (up to 3x) instead of failing the item outright.** Undeclared JSON keys are rejected by name rather than silently dropped, and truncated or refused replies fail fast rather than burning retries.

- **`prompt_sha256` and `extractor_sha256` now cover the full effective prompt** — shared system message, role prompt, and generated schema, not just the markdown — so a schema change is reflected in row provenance and cohort hashing.

- **`extract_reading_card` now reports `total_model_time_ms` instead of `wall_clock_ms`.** The old figure assumed the two structured calls ran in parallel, which they no longer do.

---

## [0.36.13] — 2026-08-28

### Changed

- **OpenAI SDK upgraded 1.109 → 2.54.** Makes `prompt_cache_options` reachable — the parameter for declaring explicit cache breakpoints, absent before 2.54 — though no code calls it yet. Capped below 3.0, which switches transport to `httpx2`.

- **Dropped the unused `ragas` dependency** from `knowledge-evals`, removing 19 packages from the lock including `datasets`, `pyarrow`, `scipy` and `huggingface-hub`. It was reserved for a generation-quality eval layer that was never built.

---

## [0.36.12] — 2026-08-28

### Changed

- **Extraction and wiki-synthesis calls now declare a `prompt_cache_key`,** so sibling calls over one prompt prefix route toward the same server-side cache rather than scattering across machines and missing. Constants: `EXTRACT_CACHE_KEY` (wiki lane), `EXTRACTION_CACHE_KEY` (three-call extractor).

- **Structured-output calls moved off the `beta` namespace** to `chat.completions.parse`. Since openai 1.92 the beta path is an alias for the same method, so this is a rename with no behaviour change.

---

## [0.36.11] — 2026-08-27

### Changed

- **Claim extraction now reliably primes the `[reported]`/`[opinion]` tag for spoken content.** It was gated on `content_shape`, an LLM's genre guess that silently skipped 58 of 124 production transcripts; `extract_claims.py`'s `_spoken_prime` now gates on `content_type in {youtube, file_audio}` instead.

- **YouTube tutorials are now primed as speech, and can be over-tagged.** On one, priming took the opinion rate from 74% to 100%, including plain definitions; the risk concentrates in the 12 of 58 newly-primed rows that are tutorials rather than talks or interviews.

- **The extract-claims eval cohort can now detect changes to the spoken prime.** Fixtures gained a `content_type` field, and `art_13` (a written tutorial) in `extract_claims_eval.jsonl` was swapped for the spoken `tut_qYNs80FKIVc`; `SCHEMA_VERSION` bumped 1 → 2.

---

## [0.36.10] — 2026-08-26

### Added

- **Two new eval fixtures pin the rewriting failure mode.** The structure-fidelity dataset now covers cases where a model paraphrases at full volume instead of collapsing, which trigram recall alone can score as faithful.

### Changed

- **The structurer's collapse guard now measures surviving wording, not output length.** A model that paraphrases at constant volume passed any length check; `call_cloud_chain` now scores trigram recall via `fetcher.fidelity`, shared with the eval harness.

- **A second guard rejects rewriting that recall alone misses.** `fidelity.long_gaps_per_10k` flags contiguous-gap density — removing filler scatters many short gaps, rewriting leaves few long ones — applied only to the transcript lane.

- **Transcripts now chunk at 12,000 characters, down from 25,000.** Fidelity degrades continuously with input length rather than at a cliff.

- **The transcript structurer tries `gpt-5.6-luna` before `gpt-4.1-mini`.** On rows the Ollama primary could not structure, `gpt-4.1-mini` scored below it.

- **The fetcher can now call gpt-5-family reasoning models.** They reject `temperature`, which `call_cloud_chain` hardcoded, so none could run in this lane.

---

## [0.36.9] — 2026-08-26

### Added

- **A fidelity eval now covers the article and transcript structurers.** `services/fetcher/evals/structure_fidelity.py` scores trigram recall against pinned fixtures; this stage previously had no coverage, since downstream evals score against already-structured text.

- **Extracted claims now record which source units they came from.** Body text is split into numbered units before extraction, and each claim cites the ones it drew on. Implemented in `domains.wiki.units`.

### Changed

- **The transcript structurer no longer collapses long transcripts.** `structure_transcript` now chunks input above 25,000 characters and rejoins; the worst production transcript went from 20% to 89% trigram recall.

- **`POST /v1/structure` no longer summarises long pasted articles.** Its old prompt said "do not summarise" while also instructing boilerplate deletion, leaving nothing to stop the model merging sentences past the first fifth of a long document. It now runs `prompts/structure_v2.md`, carrying an explicit preservation contract.

- **A collapsed structuring completion is no longer cached and re-served.** `call_cloud_chain` now rejects completions below 35% of input length (60% for transcripts), falling through to a retry instead of writing a bad result into the 365-day cache.

- **Claims and entities extractions now record a `_v2` prompt label.** The shared system prompt moved to `extract_shared_system_v2.md`, so rows extracted before and after are no longer comparable under the same label.

---

## [0.36.8] — 2026-08-24

### Changed

- **The wiki sidecar names its user-notes flag for what it means.** `_index/resolve.json` carries `has_user_claims` beside the legacy `has_derived` — same value, both written — computed by `has_user_claims_for_entity` (renamed from `has_derived_for_entity`). The old name described how the flag was derived, not what it says.

- **`_SCHEMA_VERSION` deliberately stays 1.** Adding a key is additive, so no consumer breaks; bumping while both keys exist would only make a consumer pinned to schema 1 reject the whole file. The bump belongs with the later removal of `has_derived`.

- **`SYNTHESIZE_WIKI_DAG_VERSION` 2 → 3.** The sidecar's shape changed, so prior materializations no longer match what the asset now emits.

---

## [0.36.7] — 2026-08-24

### Changed

- **A promoted note is no longer stored as pipeline-generated text.** `claims.claim_kind` split into two axes: `provenance` (`source` / `user` / `derived`) and `stance` (`reported` / `opinion`, NULL otherwise). Notes now carry `provenance='user'` instead of `derived`, which meant "the pipeline produced this."

- **A claim that renders on no page section now logs why.** Nothing yet emits `provenance='derived'` claims (pipeline-merged, no page section exists for them); `render_attributed_markdown` drops them and logs a warning naming the entity and count instead of losing them silently.

- **Deployed wiki databases need a manual migration, run before this release deploys.** `wiki.sql` only creates the table if missing, so the schema change alone doesn't reach a live db, and the assets fail against an unmigrated one. Run `scripts/migrations/2026-08-24_claims_provenance_stance.sql` by hand; it is covered by a new automated test.

---

## [0.36.6] — 2026-08-24

### Added

- **Labelling codebook for the narrative-coverage gold** (`packages/evals/datasets/narrative_coverage_codebook.md`) — thread definition, grain test and tie-breaks, handed verbatim to a labeler, so it carries no prior labels or outcomes. Why the counts are union-merged upper bounds is recorded in the dataset section instead.
- **`narrative_v3` extraction prompt adds a voice-agent delivery layer** — speakers, structure, load-bearing claims, and 4-6 entity-chained beats the agent can walk one turn at a time, carrying `narrative_v2`'s sections unchanged. Output is pinned to English in Latin script, since raw non-Latin text isn't speakable by TTS. Not active until `PROMPT_LABEL_NARRATIVE` bumps.

### Changed

- **The narrative-coverage gold now reaches the short end of the corpus.** Three fixtures added (10 total): the size floor drops 15,449 → 1,574 characters and `youtube` rises 29% → 40%, against a corpus that is 53% YouTube. Scores are not comparable across `gold_version`.
- **`eval-narrative-coverage` reports coverage per `content_type`.** The harness computed it and the CLI discarded it, so a `facebook`-specific regression had nowhere to appear. Prints alongside `content_shape` in the per-arm block, the delta block and the Notion row.
- **The dataset README documents each dataset as a contract rather than its contents.** Five headings per dataset; derivable counts and fixture tables dropped after the coverage section claimed 7 sources / 137 threads against a file holding 10 / 206.
- **Extraction can be pointed at a dotted `gpt-5` release.** `_token_kwargs` sent `reasoning_effort="minimal"`, which every release from `gpt-5.4` on rejects with a 400, while the original generation rejects `none`. Now split on the dot; `gpt-5-mini` is unchanged.
- **The wiki judge's cost is no longer reported as $0.00.** `gpt-4.1` was absent from `PRICING_PER_1M` and is the only model any caller costs. Added with the two extraction models; `gpt-4o-mini` deliberately omitted, since as a prefix key it would misprice `-tts` and `-transcribe`.

---

## [0.36.5] — 2026-08-21

### Changed

- **`recall` finds recently-fetched articles again.** An item whose text chunks to nothing got no vector, so `pending` never saw it as done and re-picked it every tick; each source now excludes its own zero-chunk items at discovery. 870 unfetched corpus rows had held the `contents` lane at 14 documents.
- **Discovery reports what it skipped.** `pending` emits `skipped_unfetched` alongside an unfiltered `total_by_source`, so a stalled fetcher no longer reads as a complete lane.
- **Emptying a note's body no longer strands its old vectors.** Local-file ids hash content, so clearing a body mints a new id; `_bodyless_awaiting_purge` re-lists the file only until its stale `source_ref` chunks are deleted.
- **A NULL dialogue message no longer fails the whole `conversations` sync for that tick.** `_serialize_turns` tolerates null `events.content` instead of raising `AttributeError`.

---

## [0.36.4] — 2026-08-21

### Changed

- **Nightly backups of newsletter-assistant's articles store work again**, after 41 straight failures caused by NA's `raw_store.db` → `corpus.db` rename (0.46.0). `snapshot_raw_store` and the vector-store contents lane now read `corpus.db`; snapshots taken before 2026-07-11 need manual rename from `raw_store.db` to `corpus.db` to restore.
- **The vector index `recall` reads no longer goes silently stale.** `run_populate_vector_store`'s schedule now defaults to `RUNNING` (was `STOPPED`), so a lost persisted Dagster schedule state can't disarm it with no error surfaced.

---

## [0.36.3] — 2026-08-10

### Changed

- **Failed Notion queue rows now name the real exception.** The Error field was showing Dagster's `DagsterExecutionStepExecutionError` wrapper; `step_failure_message` (`defs/shared/run_failure.py`) now walks the step error's `cause` chain and reports the innermost link carrying a message. An explicit `dg.Failure(description=...)` still takes precedence.

---

## [0.36.2] — 2026-07-24

### Changed

- **Extraction supports gpt-5-family reasoning models.** `ThreeCallOpenAIExtractor` sends `max_completion_tokens` + `reasoning_effort="minimal"` for `gpt-5*` models (which reject `max_tokens`), keeping `max_tokens` for gpt-4.1/4o. Enables `EXTRACT_QUEUE_MODEL=gpt-5-mini`, which lifts narrative faithful_recall +0.09–0.12 on the narrative-fidelity floor. Implementation: `_token_kwargs` in `three_call_openai.py`.
- **`narrative_v2` extraction prompt discourages over-splitting.** A "one thread per distinct point" rule so a short source yields few threads instead of one point inflated across many — curbs the over-production gpt-5-mini shows on simple content.

---

## [0.36.1] — 2026-07-23

### Added

- **Narrative-fidelity floor eval for `narrative_v2`.** Gold seed (`narrative_fidelity_gold_seed.jsonl`, 11 reader-anchored fixtures with `critical_threads`) plus `NarrativeFidelityScorer` scoring omission / corruption / invention against gold via two cross-family judges with conservative merge. Metrics in `evals.extraction.fidelity`: `faithful_recall` / `distortion_rate` / `fabrication_rate` / `severe_omission_count`.
- **Human-calibrated fidelity rubric** (`DEFAULT_FIDELITY_PROMPT`): the conclusion test — dropped derivable scaffolding stays `faithful`, a figure stripped of meaning-carrying context is `distorted`, a point collapsed to a generic restatement is `absent`. Pins grading so the floor doesn't swing with judge strictness.

---

## [0.36.0] — 2026-07-21

### Changed

- **Every eval run now records a `RunManifest` provenance envelope** — dataset, schema, subject, subject/judge models, code rev, gate-vs-report mode, N runs — attached in `RunRecord.config` (extraction/coverage), the result JSON (`eval-retrieval`), or the printed report (`eval-extract-claims`), giving one consistent "what produced this score" answer. Implementation: `evals.core.manifest`.
- **Eval scoring runs through one shared runner** — `evals.core.run_and_report` (score → stratified aggregate → persist) + `run_repeated` (N-run mean+observed-range), replacing the per-harness loops in the extraction benchmark and `eval-narrative-coverage`. Retrieval and extract-claims keep their own run shapes; only the manifest is shared.

### Removed

- **`eval-extraction` console script** — a never-wired stub. `eval-narrative-coverage` is the extraction eval CLI; topic-card scoring runs through the workbench notebooks / `run_benchmark`.

---

## [0.35.1] — 2026-07-18

### Changed

- **Narrative extraction now covers the whole source, not a fixed ~8-section basket** — the `narrative_v2` prompt is threads-first (catalogue every follow-up thread, list item, and figure), lifting gold-set coverage 71%→85% at gpt-4.1-mini. `max_tokens` raised 2048→4096. Implementation: `prompts/extraction/narrative_v2.md`, `PROMPT_LABEL_NARRATIVE`.
- **Extraction prompt design-notes headers no longer reach the model** — each `prompts/extraction/*.md` header (above the first `---`) is stripped at load via `domains.extraction.strip_design_notes`, at every read site. Model-facing body unchanged.

### Added

- **Narrative coverage is now a scored, re-runnable eval** — `NarrativeCoverageScorer` judges each narrative present/absent against a pinned 137-thread gold set (`packages/evals/datasets/narrative_coverage_gold.jsonl`). The `eval-narrative-coverage` CLI re-runs N times (default 3; headline is mean + range), diffs a `--baseline` prompt, and reports `coverage@present` by content shape.

---

## [0.35.0] — 2026-07-13

### Added

- **GitHub repos now ingest their README** — a new `github` handler fetches `raw.githubusercontent.com/<org>/<repo>/HEAD/README.md` instead of the article catch-all (which 403s on GitHub and mis-prompts the extractor on code); no README → the manual-paste error-state. Implementation: `fetcher.handlers.github`.

### Changed

- **Content Type is now a lowercase routing taxonomy** — `youtube`/`arxiv`/`medium`/`facebook`/`github`/`file_pdf`/`file_audio`/`article` (+ `other` override). Triage and the fetcher route on one shared source (`domains.classify_url_type`), so Medium/Facebook/GitHub surface instead of hiding under `Article` and a URL's type always matches its fetch handler. Requires matching Notion "Content Type" options + a one-time row migration.
- **The fetch cascade walks each handler's tiers in declared preference order** rather than all-free-then-paid, so a quality-first handler (arXiv: LlamaParse then pymupdf) is honoured; `allow_paid` still gates paid tiers. Implementation: `fetcher.cascade.run_cascade`.
- **A paywalled article's publish date now survives body rejection** — the Jina tiers opt in via `Tier.carry_meta_on_reject` to carry their structured `Published Time` onto the winning tier; heuristic scrapers stay opt-out so a wrong date can't leak onto a good fetch.
- **arXiv fetches are more robust** — a non-2xx PDF response fails the tier cleanly instead of extracting garbage from the error page, and arXiv PDFs are 50MB-capped like generic PDFs. Implementation: `fetcher.handlers.arxiv` via shared `handlers/_pdf_download.py`.
- **`.opus` audio files now transcribe** instead of falling to the article handler. The file-matched handlers are renamed to match the taxonomy (`podcast`→`file_audio`, `pdf`→`file_pdf`), suffix set unified via `domains.AUDIO_SUFFIXES`.

---

## [0.34.1] — 2026-07-12

### Added

- **Wiki claims and notes now carry a content-published date.** The fetcher captures it free per source (Jina `Published Time:`, trafilatura HTML metadata, arXiv/Facebook API fields, and the YouTube watch-page `uploadDate` via the SOCKS5 proxy — no yt-dlp), preserved across tier fallback and normalized to `YYYY-MM-DD`. Implementation: `fetcher.metadata.build_metadata`, per-handler tiers, `cascade` metadata carry.
- **A Notion "Publish Date" property sets an item's date, both ways.** Triage reads it into `queue_items.content_date`; the fetch stage writes the fetcher-discovered date back to Notion so it's visible. User-set wins (`COALESCE` in `upsert_fetched`); the fetcher only fills a blank. Requires adding a `Publish Date` date property to the Queue database.

### Changed

- **Wiki source claims render as `[domain](url)` backlinks with distinct published + fetch dates**, and **promoted-note claims backlink to their origin note file** (`data/notes/<note_id>.md`). Implementation: `domains.wiki.attributed._attribution`, `wiki_synthesis.promote_notes`.
- **NA's appended note footer is stripped before a note becomes claim text** (both `---`-delimited and bare `Source:`/URL shapes). Implementation: `notes.promoted._strip_note_trailer`.

---

## [0.34.0] — 2026-07-11

### Added

- **Promoted notes now render on their entity's wiki page under `## From my notes`.** `render_attributed_markdown` surfaces each `derived` claim as a verbatim markdown block (not a flattened bullet), captioned with the note title + date, kept separate from the source-side Reported/Opinion sections. An entity carrying only a promoted note is exempt from the page-worthiness floor, so a note that mints a fresh entity still gets a page. `resolve.json` entity entries gain `has_derived` so NA / MCP can flag pages holding the user's own synthesis.
- **Wiki pages now emit a deterministic `summary:` frontmatter field** (the lead claim's first line — reported, else opinion, else the promoted note). Fixes the empty-string wiki embed: the vector lane reads `meta["summary"]`, so the `wiki` collection now embeds real text instead of nothing. (A crude heuristic; an LLM summary + a body/summary embedding eval are the deferred follow-up.)

---

## [0.33.0] — 2026-07-10

### Added

- **User-promoted notes become attributed `derived` claims on entity-wiki pages.** New `synthesize_wiki/promote_notes` asset resolves a note's `entities` hints against the wiki (exact-name + alias, denylist-gated; a miss mints a `concept` entity) and writes one `derived` claim per note, linked to every resolved entity — idempotent and reconciling. Adds `derived` to `claims.claim_kind` (typed-separate from source reported/opinion); `SYNTHESIZE_WIKI_DAG_VERSION` → 2.

---

## [0.32.4] — 2026-07-09

### Changed

- **Wiki dedup name pass drops version/size variants.** `find_name_candidates` (`domains.wiki.dedup`) now skips a pair when both canonical names carry digits that differ (`Claude Opus 4.5`/`4.7`, `Qwen 7B`/`72B`) — version variants are never the same entity, so they no longer reach the human merge gate as false candidates. Same-digit punctuation twins and one-sided-digit pairs (`World War II`/`2`) still surface.
- **Wiki entity names prefer the singular canonical form.** The entity-extraction prompt (`prompts/wiki/extract_entities_task_v1.md`) now asks for the singular ("Code review", not "Code reviews"), and the curation runbook (`domains/wiki/CURATION.md`, refreshed to cover `wiki-merge`) documents singular-survivor as the merge convention — page titles stay consistent and plural/singular twins stop accumulating.
- **Wiki entity extraction discards truncated (degenerate) output.** When an `extract_entities` call hits the output-token cap (`finish_reason == 'length'`, now surfaced on `workflows.llm.LLMCall`), the model has degenerated into a repetition loop emitting hundreds of near-duplicate run-on names — the whole set is discarded and logged, not minted. Root cause of the hand-deleted "hallucination cluster" junk entities.

---

## [0.32.3] — 2026-07-09

### Added

- **Wiki dedup now catches a claim-rich entity's thin name-twin.** The merge-candidate search gained a lexical name-only pass (`domains.wiki.dedup.find_name_candidates`) unioned into `evals.wiki_dedup.openai_candidates` alongside the embedding pass. The embedding pass embeds name + claim texts, so it's claim-weighted and a claim-heavy entity never pairs with its claim-empty duplicate (an 18-claim `Agent harness` missed `Agentic harness`); the name pass keys on the canonical name alone and recovers exactly that case.

---

## [0.32.2] — 2026-07-07

### Changed

- **Extract op dispatches on `canonical_url`, not raw `url`.** Substack/newsletter redirects that triage resolved to a YouTube video (or any other handler) no longer crash the fetcher with "all tiers failed" — the raw redirect URL never reaches a handler that can't parse it. Fails loudly if canonical is missing.
- **`*.medium.com` author subdomains route to the Medium handler.** `medium.matches()` was exact set-membership against `medium_domains.yaml`, so `pravash-techie.medium.com/…` fell through to the article handler and hit the paywall without RapidAPI's bypass tier.

---

## [0.32.1] — 2026-07-06

### Changed

- **Edited notes and briefs no longer return stale text from before the edit.** Local-file lanes hash content into `content_id`, so an edit minted a new id while the pre-upsert delete only matched the new one — old-hash chunks lingered. The delete in `_process_item` now also matches the stable `source_ref` (`local:{filename}`) via `_delete_where`, purging prior-hash chunks on re-ingest.

---

## [0.32.0] — 2026-07-06

### Added

- **Wiki vector lane** — `populate_vector_store` now indexes synthesized wiki entity summaries into a `wiki` Chroma collection, so newsletter-assistant's `recall` can search entity pages, not just raw content / sessions / notes. Each wiki vector carries provenance metadata: `num_sources` (lets the recall side hedge a single-source page), plus `page_hash` + `snapshot_id` from `_index/resolve.json` (stale-hit detection). The wiki source is kp-owned — it roots at `LOCAL_WIKI_DIR` (`DATA_DIR/wiki`), not the newsletter-assistant `BACKUP_SRC_DIR` mount the other sources read.
- **Wiki freshness re-embed** — wiki pages are rewritten daily but keep their `entity_id`, so a bare existence check would serve a stale vector forever. Discovery now re-lists a wiki entity whenever its live `page_hash` (from `resolve.json`) differs from the indexed one; immutable lanes (raw_store) keep the cheap existence check.
- **Briefings vector lane** — `populate_vector_store` now indexes newsletter-assistant briefs (`briefs/*.md` under `BACKUP_SRC_DIR`) into a `briefings` Chroma collection, so `recall` can search them separately from notes. A straight `LocalFileSource` mirror of the notes lane — content-hashed ids, frontmatter markdown, no provenance/freshness (an edited brief gets a new id and re-ingests naturally).

### Changed

- **`conversations` lane now reads the `events` table, not `turns`.** `SessionsSource` indexed the legacy `turns` table unfiltered, so `tool_call` / `tool_result` rows (agent machinery — raw tool JSON, fetched payloads already indexed in `contents`) leaked into the `conversations` collection as recall noise. It now reads newsletter-assistant's canonical `events` stream filtered to `user_msg` / `assistant_msg`, dropping the tool machinery and shedding the dependency on the deprecated dual-write table.

---

## [0.31.2] — 2026-07-04

### Added

- **Curated entity dedup (#15)** — new `wiki-merge` CLI folds a duplicate wiki entity into a survivor: one `merge_entities` transaction re-points `claim_entities` + `aliases` drop→keep, aliases the dropped name onto keep (so it can't re-mint), deletes the duplicate, and bumps keep's page. `--no-alias` keeps homonyms separate; `--backup` snapshots `wiki.db` first; `--dry-run` rolls back. Re-rendering the survivor is a separate post-batch `render_pages` sweep.
- **Merge-candidate search (#15)** — `evals.wiki_dedup.openai_candidates(db)` embeds each entity's `name + top claim texts` and returns high-cosine near-duplicate pairs (default `text-embedding-3-small`, cosine ≥ 0.8), the input to the agent-driven CLUSTER → JUDGE → CONFIRM → MERGE loop. Catches diverge-names/converge-meaning dups (e.g. `Claude Max` ≡ `Max plan`) the in-synthesis fuzzy matcher misses.

### Changed

- **`OpenAIEmbedder` now chunks embedding requests by item count (≤ 2048), not only by token budget.** OpenAI's `/embeddings` caps the input array at 2048 items regardless of tokens; a many-short-texts corpus (the ~2.9k wiki entities) overflowed in one request → HTTP 400. Fixes the merge-candidate search on the full corpus.

---

## [0.31.1] — 2026-07-04

### Added

- **One-shot extraction backfill** (`scripts/backfill_extraction_from_queue.py`) — re-runs the attributed-lane `extract_claims` + `extract_entities` over `queue.db` rows fetched before those assets existed, so they gain the docs the wiki sweep needs. Bypasses Dagster (the sensor only reprocesses Status=Fetching rows and old partitions are unregistered); calls the same extract functions, records identical `extraction_calls` rows. Dry-run by default; `--apply` to write.

---

## [0.31.0] — 2026-07-03

### Added

- **Wiki synthesis regains its `index.md` TOC and `_index/resolve.json` sidecar** — a new `build_index` asset rebuilds both from `wiki.db` each tick, restoring the newsletter-assistant bridge. `resolve.json` maps aliases / canonical names / entity_ids (self-mapped) to entities with per-entity orientation (`file`, `num_sources`, `page_hash`) and a whole-wiki `snapshot_id`; `index.md` is a human TOC. Written last, only on change, self-healing.

### Changed

- **Curator rejections now hold in the attributed wiki lane** — a rejected entity name no longer re-mints and re-earns a page when new claims mention it. Entity assignment drops denylisted (`rejected_entities`) candidates before resolution; a claim also naming a rejected entity routes to subject-attribution instead of collapsing onto the lone live mention.

---

## [0.30.1] — 2026-07-03

### Added

- **Wiki pages regain a `related` list** — each entity page's frontmatter now lists the other entities it co-occurs with, ranked by how many sources co-mention the pair. Co-occurrence is **derived at render time** from `claim_entities` (two entities relate when both are claimed within the same source), so it stays consistent with the current claims: a re-extraction that changes a source updates every affected page's `related` on the next render with no edge upkeep.

### Removed

- **The orphaned `entity_relations` ledger table** (and `insert_entity_relation`) — a leftover from the retired raw-article path that the attributed lane never wrote. Co-occurrence is now derived from `claim_entities` rather than materialised as edges, so there's no second projection to keep consistent. Fresh `wiki.db`s omit the table; the clean-slate deploy rebuilds from `wiki.sql`.

---

## [0.30.0] — 2026-07-02

### Added

- **New `synthesize_wiki` Dagster DAG** — the wiki-write lane (`attribute_claims` → `render_pages`) carved out of `fetch_extract_queue`. The DAG boundary is now the store seam: `fetch_extract_queue` writes `queue.db` + Notion only, and `synthesize_wiki` reads `queue.db`'s stored `extract_claims`/`extract_entities` docs and writes `wiki.db`. Runs as a daily unpartitioned sweep (06:00 UTC, before the 07:00 curation tick), decoupling synthesis from the per-row Notion fetch path.
- **Incremental synthesis watermark** — a new `wiki.sources.synthesized_at` column (= the `max(extracted_at)` a sweep consumed). The sweep skips a source whose extraction docs haven't advanced past its watermark and re-processes re-extracted sources with their claims replaced (not merged); a re-extraction that shrinks an entity below the page-worthiness floor prunes its stale page. Docs + watermark are read from one queue.db snapshot (`get_ready_extraction_docs`) and sources are deduped by `content_key`, so a concurrent extraction or a duplicate URL can't advance the watermark past unconsumed docs.

### Changed

- **The former per-`page_id` partitioned wiki persist is now an unpartitioned sweep.** `persist_attributed_claims` / `render_attributed_pages` (in `fetch_extract_queue`) are replaced by `synthesize_wiki`'s `attribute_claims` / `render_pages`; the render skips entirely on an empty sweep so untouched pages keep their `updated_at`. Both assets keep the shared `WIKI_WRITE_POOL` op tag. `WikiWriteResource` is deleted — synthesis reuses the shared `wiki` resource. **Deploy note:** the asset keys move (`fetch_extract_queue/*` → `synthesize_wiki/*`), resetting those keys' Dagster materialization history; `wiki.db`/`queue.db` state is untouched. Add the `synthesized_at` column on the live `wiki.db` (`ALTER TABLE sources ADD COLUMN synthesized_at TEXT`; fresh DBs get it from `wiki.sql`). Deploy the new DAG before disabling the old render schedule.

---

## [0.29.1] — 2026-07-02

### Changed

- **`fetch_extract_queue`'s reader-lane assets are renamed to a verb-led convention** — `fetched`→`fetch_content`, `extracted`→`extract_reading_card`, `published`→`publish_item` (the attributed-lane assets were already verb-led). **Deploy note:** renaming a Dagster asset key resets that key's materialization history in the UI; the underlying `queue.db` state is untouched, so pipeline behaviour is unchanged.

---

## [0.29.0] — 2026-07-02

### Removed

- **The raw `synthesize_wiki` Dagster pipeline is retired** (assets, schedule, its `wiki.db` writes). Entity-wiki pages are now produced solely by the attributed lane in `fetch_extract_queue`; the shared `wiki` resource + `WIKI_WRITE_POOL` concurrency key moved to `orchestrators.config`/`shared`.
- **The raw-article synthesis code path is deleted** — `wiki_synthesis/synthesize.py`, `domains/wiki/salience.py` + `relevance.py`, and the `page_synthesis_*` / `entity_extraction_*` prompts. `count_mentions` (still used by attributed-lane claim matching) moved to `domains/wiki/mentions.py`; the vestigial per-source `salient` flag on `SummaryAssignment`/`EntityClaims` was dropped (page-worthiness is derived from claim evidence).
- **The legacy wiki page ledgers are dropped** — the `page_sources` and `page_versions` tables (plus the `pages.content_hash` / `pages.current_version` HEAD pointers), page edition-history (`page_content_hash`, `get_page_history`, `get_page_version`), and the entity-dedup CLIs `wiki-merge` (`merge_entities`) + `wiki-dedup-candidates`. Both tables were write-frozen once the raw path went; a page's source count is now derived on read via `attributed.count_sources_for_entity`. `wiki-reject` and the `entity_relations` ledger survive.

### Changed

- **The `wiki` Dagster resource now lives in `orchestrators.defs.shared`** (moved from the retired `synthesize_wiki` package) so it's owned by a surviving location. Wiring-only; no behaviour change.
- **`entities.page_type` is renamed to `entity_type`** across the wiki state layer, assets, and the on-disk page frontmatter key — the field labels an entity's kind, not a "page". **Deploy step:** run `ALTER TABLE entities RENAME COLUMN page_type TO entity_type` on the live `wiki.db` (fresh DBs get the new name from `wiki.sql`). The Notion "Page type" property display name is unchanged; the `.md` frontmatter key is rewritten to `entity_type:` by the next render sweep.

---

## [0.28.1] — 2026-07-02

### Changed

- **Attributed wiki pages now split claims into `## Reported` / `## Opinion` sections** instead of one flat list — `render_attributed_markdown` groups by `claim_kind` and drops the redundant inline tag, so facts and takes are distinguishable.

---

## [0.28.0] — 2026-07-02

### Added

- **Entity extraction for the attributed lane** — `extract_entities(article, claims)`
  (`workflows.wiki_synthesis.extract_entities`) reads the raw article alongside its
  claims, recovering the article's implicit subject and long-tail entities that
  claims-only extraction missed; no entity cap (salience classifies the tail downstream).
- **Entity candidates are now extracted and stored at fetch/extract time** — new
  `fetch_extract_queue/extract_entities` Dagster asset records per-source candidates
  (`extraction_calls` `call_kind='extract_entities'`), sharing the article
  prompt-cache with `extract_claims`. The attributed lane's candidate set.
- **Attributed-lane wiki pages** — entity pages now render from source-attributed claims persisted in `wiki.db` (new `sources`, `claims`, `claim_entities` tables; `domains.wiki.attributed`). Page-worthiness floor: ≥2 claims or ≥2 sources required to emit a page; the existing raw-article synthesis path is unchanged.
- **Two new `fetch_extract_queue` Dagster assets** — `persist_attributed_claims` writes per-source claim sets to `wiki.db` via the new `wiki_write` resource (serialized on a dedicated write pool); `render_attributed_pages` sweeps all entities daily and rebuilds attributed pages, scheduled at 07:00 via its own job.

### Changed

- **Claim-tag accuracy improved on Medium-shaped content** (opinion-essay, tutorial,
  unknown) — restructuring `extract_claims` to a shared prompt-cache prefix
  (`extract_shared.shared_prefix_messages`) fixed a systematic opinion-under-tagging
  bug; faithfulness held at 100% across the eval cohort.

---

## [0.27.5] — 2026-07-01

### Changed

- **The per-source claim step is renamed from "summarize" to "extract"** — it does
  faithful claim extraction, not summarization. `summarize_source` →
  `extract_claims` (module `workflows.wiki_synthesis.claim_extractor`),
  `SourceSummary` → `ClaimSet` (module `domains.wiki.claims`), the
  `eval-source-summary` CLI → `eval-extract-claims`, the `source_summary`
  extraction-calls `call_kind` → `extract_claims`, and the prompt assets →
  `prompts/wiki/extract_claims_*_v1.md`. `SourceClaim` is unchanged.

---

## [0.27.4] — 2026-07-01

### Added

- **Attributed-lane entity assignment (Slice 2)** maps each source-summary claim
  to the entity it is about (`workflows.wiki_synthesis.entity_assignment`).
  Extraction runs over the claims, entities resolve against the LIVE wiki via the
  same `resolve_or_mint_batch` the raw-article path uses (so a claim unifies onto
  an existing entity instead of minting a duplicate). The deterministic
  surface-form match is a hint: a claim naming exactly one entity is assigned it;
  ambiguous claims (a pronoun, or a possible passing co-mention) go to one closed
  subject-attribution LLM call over the whole claim list
  (`prompts/wiki/subject_attribution_*_v1.md`) that returns each claim's true
  subject(s) from the candidate set — so "Microsoft ditches OpenAI" is attributed
  to Microsoft, not both. `group_by_entity` inverts to per-entity attributed claim
  sets, flagging passing co-mentions via the shared salience gate. Persists
  nothing yet.
- **Entity-assignment diagnostic over the source-summary corpus**
  (`evals.wiki.source_summary.assignment_report`) reports claim coverage, the
  salient-vs-co-mention split, and orphaned claims — the substrate for judging
  assignment precision before attributed pages are built.

---

## [0.27.3] — 2026-07-01

### Changed

- **Source summariser tags the author's analytical framing as `[opinion]`.** A
  third "hides as reported" trap (`prompts/wiki/source_summary_system_v1.md`)
  covers strategic interpretation, risk/threat assessment, and stated intents
  ("aims/expects to …") — so news/commentary analysis is attributed, not recorded
  as fact.

---

## [0.27.2] — 2026-07-01

### Changed

- **Confidence-lane gate no longer corroborates two low-credibility sources.**
  `route_lane` granted `CORPUS_CORROBORATED` on ≥2 sources without checking
  credibility; two Medium-tier echoes now stay `SINGLE_SOURCE_ATTRIBUTED`
  (corroboration requires a non-LOW source in the cluster).
- **Gate collapses echoed sources and normalises domains before counting
  corroboration.** `count_independent_sources` folds near-duplicate / republished
  sources (embedding-close) into one; `domain_credibility` lowercases, strips
  `www.`, and matches by registrable suffix so subdomains inherit the tier, over
  expanded HIGH/LOW allowlists.

### Added

- **Gate diagnostic over the source-summary corpus** (`evals.wiki.source_summary.gate_report`)
  wires the previously eval-only confidence-lane gate to production data —
  `credibility_of` + deterministic `is_specific` adapters + `build_gate_report`
  (lane distribution, parse failures) — plus `domains.queue_store.get_all_source_summaries`
  (latest summary per page). First slice of the attributed-lane downstream wiring;
  no persisted schema or entity pages yet.

---

## [0.27.1] — 2026-07-01

### Changed

- **Source-summary claim tags renamed `fact`/`speculation` → `reported`/`opinion`**
  (producer prompt markers, the `domains.wiki.source_summary` parser/renderer, the
  tagging judge, the eval harness). "fact" wrongly implied *true* when it only meant
  *reported-as-established*. The internal `SourceClaim.speculative` bool is unchanged
  (`True` ⟺ `[opinion]`), so the gate is untouched; no DAG-version bump.

### Added

- **Source-summary eval harness** (`evals.wiki.source_summary` + `eval-source-summary` CLI):
  pinned 12-source cohort, faithfulness scorer, `TaggingJudge`, benchmark, and
  calibration against a human gold.
- **Human-labelled tagging gold** (`datasets/source_summary_tagging_gold.jsonl`).
  60 claims, 6 content shapes, user-labelled. Producer 85% vs gold (exact; weak only
  on `unknown`-shape news, over-tagging unverified claims as `reported`); judge
  ~90% (88–98% across runs — noisy, anchored). Taxonomy in `packages/evals/datasets/README.md`.

---

## [0.27.0] — 2026-06-30

### Added

- **Fetched queue rows now record the article's title, author, and publication
  date.** The `fetched` asset persists the fetcher's `title` / `author` /
  `content_date` onto `queue_items` (three new columns) instead of only logging
  them as run metadata — making a queue row self-sufficient for downstream source
  summarisation (no raw_store join). Cleared on re-triage with the rest of the
  cohort's fetch state.
- **Per-source claim summaries produced at fetch time.** A new
  `fetch_extract_queue/source_summary` asset distils each fetched body into
  content-shape-aware `[fact]`/`[speculation]` claims (`summarize_source`) and
  records them as a `source_summary` `extraction_calls` row — the attributed-lane
  wiki substrate. Read via `domains.queue_store.get_source_summary`.

---

## [0.26.5] — 2026-06-29

### Added

- **Per-source claim summaries (Layer 1.5) for the attributed-lane wiki.**
  `summarize_source` (`workflows.wiki_synthesis.source_writer`) distils one
  source into `[fact]`/`[speculation]`-tagged claims attributed to it — the
  substrate the entity writer and confidence-lane gate read instead of raw
  articles. Tagging is content-shape-aware (spoken sources bias the speaker's
  forecasts/opinions to `[speculation]`) and runs at `temperature=0` to keep
  summaries low-variance run-to-run. Types + parse/render/slug live in
  `domains.wiki.source_summary`.

---

## [0.26.4] — 2026-06-29

### Added

- **Offline confidence-lane admission gate for wiki synthesis.** Routes each claim
  into a lane (corpus-corroborated / single-credible / attributed / open) by
  claim agreement, source credibility, a specificity floor, and a speculation tag —
  rather than asserting everything as fact. Pure logic with injected
  embeddings/specificity in `evals.wiki.gate`; foundation for the wiki shadow audit.

---

## [0.26.3] — 2026-06-29

### Changed

- **URL-fetched articles no longer carry Jina Reader's metadata preamble into
  extraction.** The fetcher strips Jina's `Title: / URL Source: / Published Time:
  / Markdown Content:` header from fetched bodies (`jina.strip_preamble`, applied
  on the Jina success path in the article + medium handlers); runs after
  upstream-error detection, so paywalled/blocked handling is unaffected.

---

## [0.26.2] — 2026-06-29

### Changed

- **User-pasted article bodies are now always boilerplate-stripped before
  extraction.** The fetcher's `/v1/structure` cascade dropped its passthrough
  short-circuit, which let muddied pastes (e.g. Medium logged-in sidebar/footer)
  skip the cleaning LLM stage; pasted content now runs `trafilatura → cloud LLM`
  every time.

---

## [0.26.1] — 2026-06-28

### Added

- **Reader comments on a queued Notion page now surface in extraction as a
  `reader_threads` artifact** — captured at triage and stored in the new
  `queue_items.user_comments_json` column, then folded into the extractor's
  followups call via a `user_notes` param, without biasing the source-grounded
  narrative or topic card.
- **`NotionQueueResource.get_page_comments`** reads page comments best-effort:
  a missing "Read comments" token capability logs a warning and returns empty
  rather than blocking triage.

---

## [0.26.0] — 2026-06-28

### Added

- **Relevance judge** (`evals.wiki.RelevanceJudge`) — a third wiki page-quality axis scoring whether a page stays on its entity or drifts into a co-occurring subject; reports `on_topic_fraction` + `drift_count` + `drift_subjects`. Provider-free; prompt at `prompts/eval/relevance_v1.md`.

### Changed

- **Wiki synthesis gates sources by entity salience.** A page is written only for entities an article is substantially about (in the title or ≥3× in the body), so a one-mention tangential article no longer pollutes it. Peripheral entities are still minted (row only). New `domains.wiki.salience`.
- **Wiki pages are synthesised from the entity's own passages, not the whole article.** Per-entity synthesis feeds only the windows around each mention (± 400 chars, overlaps merged) instead of the full source, cutting off-topic drift. New `domains.wiki.entity_windows` in `synthesize_entity`.

---

## [0.25.1] — 2026-06-25

### Added

- **Wiki page-quality eval** (`evals.wiki`) ships two judges: `FaithfulnessJudge` scores claim grounding against source passages; `SpecificityJudge` combines deterministic number/date anchor recall with LLM-judged name/quote preservation and an abstraction penalty. Provider-free via injected `chat_fn`; production wrappers target structured gpt-4.1 output. Judge prompts versioned under `prompts/eval/`.

---

## [0.25.0] — 2026-06-25

### Removed

- **Deprecated research-panel source dropped end-to-end.** `backup_readings` no longer snapshots `research.db` (`BACKUP_READINGS_DAG_VERSION` 6 → 7) and `populate_vector_store` no longer embeds it (`research_documents` asset + collection gone; `POPULATE_VECTOR_STORE_DAG_VERSION` 2 → 3). Deletes the `domains.research.ResearchSource` adapter, the `evals.retrieval` `--research-db` flag + `research` source, and the 21 advisory `research` pairs from `retrieval_eval.jsonl` (166 → 145).

---

## [0.24.12] — 2026-06-24

### Changed

- **`sync_wiki_curation` push skips unchanged rows.** `push_wiki_pages` re-writes a Notion row only when its entity's `page.updated_at` (minute precision) differs from the stored `Last updated`, or its status needs re-asserting — cutting the daily push to the changed set. `merge_entities` now bumps the survivor's `updated_at` so a merge can't leave Notion stale.

---

## [0.24.11] — 2026-06-24

### Added

- **`sync_wiki_curation` DAG** adds a daily (07:00 UTC) two-way sync between wiki.db and the Notion "Wiki Pages" review DB. `wiki/rejections_pulled` imports curator `Rejected=true` toggles into `rejected_entities`; `wiki/pages_pushed` projects every current entity up to Notion with producer-only columns, flipping departed entities to `Page status=orphaned`. Requires `NOTION_WIKI_TOKEN`; `SYNC_WIKI_CURATION_DAG_VERSION` 1.

---

## [0.24.10] — 2026-06-24

### Changed

- **Notion access is now per-database for least privilege.** The single `NOTION_INTEGRATION_TOKEN` is replaced by `NOTION_QUEUE_TOKEN` (Knowledge OS Queue DB — `triage_knowledge_queue` + `fetch_extract_queue`) and `NOTION_WIKI_TOKEN` (Wiki Pages DB — reserved for the forthcoming `sync_wiki_curation` DAG). Each integration is connected only to its own DB, so a leaked token can't reach the other. `NOTION_WIKI_PAGES_DATA_SOURCE_ID` is also renamed `NOTION_WIKI_DATA_SOURCE_ID` to match the `NOTION_{QUEUE,WIKI}_*` pattern. **Deploy:** set the new env vars; `NOTION_INTEGRATION_TOKEN` is no longer read.

---

## [0.24.9] — 2026-06-24

### Added

- **`wiki-merge` CLI folds duplicate wiki entities into one** (`domains.wiki.merge_cli` → `merge_entities`): re-points the page_sources / entity_relations / aliases ledgers, aliases the dropped name onto the survivor so future mentions don't re-mint, and deletes the dropped page. `--no-alias` for homonyms; `--dry-run`.
- **`wiki-reject` CLI deletes + tombstones noise entities** (`domains.wiki.reject_cli` → `reject_entity`): denylists the canonical name and every alias into the new name-keyed `rejected_entities` table (`wiki.sql`) so it can't re-mint, and removes the page. By `--entity` or `--name`.
- **`wiki-dedup-candidates` CLI proposes near-duplicate entities for merging** (`evals.wiki_dedup`): embeds each entity's `name + summary` and prints high-cosine pairs as JSON, catching semantic dups (`Claude Max` ≡ `Max plan`) the in-synthesis difflib matcher misses. Reads prod over Tailscale via `--datasette-url` (no file copy) or a local wiki.db (`--db`/`--wiki-dir`); embeds on OpenAI or, free + local, any OpenAI-compatible server via `--embed-base-url` (e.g. `llama-server --embeddings`; `--embed-prefix` for models like nomic-embed). Pure search in `domains.wiki.dedup`; runbook in `domains/wiki/CURATION.md`.

### Changed

- **Wiki synthesis reads the denylist from the local `rejected_entities` table, not live Notion.** The per-tick `query_rejected()` + fail-closed `rejected.json` snapshot are gone, so synthesis no longer depends on Notion. `SYNTHESIZE_WIKI_DAG_VERSION` → 12.
- **An alias can no longer contradict the denylist** — `insert_aliases` drops any normalized alias already in `rejected_entities`.
- **`OpenAIEmbedder` now drives any OpenAI-compatible embeddings server** via `base_url` + `dims=None` (skips the OpenAI-only `dimensions` param), e.g. a local llama.cpp `llama-server`. Backward-compatible (OpenAI stays the default); also realigns responses by their `index` so a reordered batch can't misalign vectors.

---

## [0.24.8] — 2026-06-23

### Added

- **The entity wiki is now in the daily backup.** `backup_readings` snapshots `wiki.db` (SQLite `.backup` + integrity check) and the rendered `data/wiki/` tree (gzip-tar + archive check), closing the last corpus piece with no recovery path. Both are kp-owned (read from this repo's `data/` dir, not `BACKUP_SRC_DIR`) and anchor on a new `wiki_store` lineage source. `BACKUP_READINGS_DAG_VERSION` → 6.

---

## [0.24.7] — 2026-06-23

### Added

- **Wiki page `related` now accumulates across every article that co-mentions an entity**, not just the latest synthesis's siblings. A new `entity_relations` ledger records co-occurrence edges (both directions, per contributing item); `domains.wiki.state.get_related_for_entity` derives a `co_count`-ranked top-N list rendered into the page frontmatter. On update, producer-owned frontmatter is stripped from the existing page before it re-enters the synthesis prompt (`parsing.strip_producer_frontmatter`). Requires the `data/wiki.db` rebuild. `SYNTHESIZE_WIKI_DAG_VERSION` → 11.

### Changed

- **Wiki page `sources` frontmatter now lists the accumulated source items** (distinct item_ids from the `page_sources` ledger), consistent with `num_sources` — previously it rendered only the latest triggering item. `write_page` renders the producer-authoritative list via `domains.wiki.state.get_source_ids_for_entity`.

---

## [0.24.6] — 2026-06-23

### Added

- **Wiki pages now keep an immutable edition history.** Each synthesis that changes a page's prose appends a full-content version to a new `page_versions` table (provenance-tagged with the content item that triggered it); a semantic-content hash over `{summary, content}` gates the append so re-runs that only touch metadata don't churn the history. Read via `domains.wiki.state.get_page_history` / `get_page_version`. Requires dropping the existing `data/wiki.db` and re-synthesising (rebuild-don't-migrate). `SYNTHESIZE_WIKI_DAG_VERSION` → 10.

---

## [0.24.5] — 2026-06-23

### Changed

- **Wiki entity extraction recovers far more named people and durable entities.** The extraction model moved gpt-4.1-nano → gpt-4.1-mini, the per-article cap raised 10 → 15, and the prompt now targets the article's author + cited individuals while ignoring site chrome (sponsor/nav/cookie/related-posts); the byline is threaded in via `item.author`. ~2× entity recall with correct typing on a 17-article prod cohort (`workflows.wiki_synthesis`, `prompts/wiki/`, `domains.wiki.types`).

---

## [0.24.4] — 2026-06-23

### Changed

- **Wiki retrieval-eval source now reads synthesized pages instead of an empty corpus.** `WikiSource` was reading the old page-type subdir layout; it now globs the flat `{slug}-{shortid}.md` files and resolves by frontmatter `entity_id` (`domains.wiki.sources`).

---

## [0.24.3] — 2026-06-23

### Changed

- **Wiki entity extraction is now domain-agnostic.** The system prompt dropped its AI/ML framing and `PageType` widened to add `person`, `organization`, `method`, `dataset`, and an `other` catch-all (`prompts/wiki/entity_extraction_system_v1.md`, `domains.wiki.types`). Entity count is gated by quality, hard-capped at 10.
- **The known-entity catalog is relevance-filtered before extraction at scale.** Above 50 entities the prompt receives only the article-relevant subset instead of the full catalog (`domains.wiki.relevance`); a no-op for smaller wikis.

---

## [0.24.2] — 2026-06-22

### Changed

- **Wiki page synthesis reuses the article across an item's entities via OpenAI prompt caching.** The page-synthesis prompts now lead with the shared `[system + article]` block (per-entity fields trail), so multi-entity items hit the cache — ~50% off cached input tokens + lower latency. Implementation: `prompts/wiki/page_synthesis_user_*.md`.
- **Wiki synthesis prompts moved to versioned `prompts/wiki/*.md` files** (loaded via `KP_PROMPTS_ROOT`, mirroring `prompts/extraction/`) — edit prompts without a code change. `workflows.wiki_synthesis.prompts` is now a loader keeping the same constant names.

---

## [0.24.1] — 2026-06-22

### Changed

- **Wiki entity extraction now runs as its own Dagster asset** (`wiki/extracted`) ahead of `wiki/synthesized`, so extraction (LLM call #1) and synthesis (LLM call #2) carry separate cost / candidate metadata and retry independently. Resolution/minting still runs snapshot-live in synthesis, preserving within-run dedup. `SYNTHESIZE_WIKI_DAG_VERSION` 8→9.
- **LLM cost metadata now prices dated OpenAI model ids** (e.g. `gpt-4.1-nano-2025-04-14`) at their base-alias rate — `cost_usd` previously reported $0 for every wiki call. Implementation: `workflows.costs` resolves via a longest-prefix fallback (`is_priced` / `_rate_for`).
- **`WIKI_MAX_PER_TICK` lowered 30 → 15** so a sequential-synthesis tick finishes in a more workable window.

---

## [0.24.0] — 2026-06-22

### Changed

- **Wiki entities now have stable, name-independent identities.** Renaming or re-slugging an entity no longer forks its page; an entity is minted once as an opaque surrogate `e_<16hex>` and resolved on later mentions via an alias-gate resolve-or-mint with cross-type dedup (`packages/domains/src/domains/wiki/identity.py`).
- **Wiki page files are now flat `{slug}-{shortid}.md` in `data/wiki/`** (was nested `{page_type}/{slug}.md` subdirs), keyed to the surrogate id (`workflows/wiki_synthesis/synthesize.py`).
- **`wiki.db` schema overhauled — rebuild required, not migratable.** New `entities` table; `pages` FKs to it and drops `page_type`/`slug`/`canonical_name`; `processed` → `processed_items`; `aliases` re-keyed on `normalized_alias`; all tables `STRICT` (`packages/domains/src/domains/wiki/schema/wiki.sql`).
- **Wiki synthesis persists crash-safely** — graph transaction, then `.md` write, then marking the item processed last — so an interrupted tick leaves no half-written entity (`synthesize.py`).
- **Extraction LLM contract trimmed:** dropped `entity_id`/`is_new`, added optional `matched_id`, and the prompt gained an anti-artifact rule (`workflows/wiki_synthesis/prompts.py`).
- **W2.5 wiki denylist is now keyed on the normalized page Title** (was Entity ID), so a reject survives id churn (`defs/synthesize_wiki/denylist.py`). `SYNTHESIZE_WIKI_DAG_VERSION` bumped 7→8.

---

## [0.23.4] — 2026-06-21

### Changed

- **Wiki summaries no longer leak code-fence / frontmatter junk.** The synthesis parser (`workflows.wiki_synthesis.parsing`) now strips a wrapping ` ```yaml `/` ```markdown ` fence, and recovers the `summary` + `title` by regex when the frontmatter YAML is malformed (e.g. an unquoted title containing a colon) instead of dumping the raw block into the summary. Fixes the parser bug behind ~7% of pages rendering an unusable summary.

---

## [0.23.3] — 2026-06-21

### Added

- **The retrieval eval harness can now score the wiki source.** `eval-retrieval` gains `--wiki-dir` (+ `--chunker-wiki`) to index wiki pages via `WikiSource`; `"wiki"` is now a valid eval-pair `source` (`expected_content_id` = page `entity_id`).
- **Wiki retrieval eval datasets** (`packages/evals/datasets/wiki_eval_{named,paraphrase}.jsonl`) — 18 leakage-guarded query→entity_id pairs over the kept wiki corpus, split into named/paraphrase slices. Baseline locks wiki indexing to summary-only (no alias/keyword/hybrid retrieval).

---

## [0.23.2] — 2026-06-21

### Added

- **Wiki pages are now readable as an ingest source** via `domains.wiki.sources.WikiSource` (`get_items` / `get_item_ids` / `get_item`), mirroring the other source adapters. Each page → one `IngestItem` with `text` = page summary, carrying a new optional `IngestItem.num_sources` field for the upcoming index-time sparsity gate. Adds `domains.wiki.io.read_meta` for frontmatter-only reads.

---

## [0.23.1] — 2026-06-21

### Changed

- **Wiki synthesis now skips items whose body hasn't been fetched yet.** `wiki/pending` filters on a new `with_body` flag in `domains.raw_store.sources.get_content_ids` (SQL `content_md` non-empty). Previously an unfetched item was synthesised empty and marked `processed`, so it was never re-synthesised once the fetcher filled it; the asset now also surfaces an `excluded_unfetched` count.
- **Wiki `num_sources` is now counted from a deterministic ledger, not the LLM-authored page frontmatter.** New `wiki.page_sources(entity_id, item_id, source_type)` table records each real contribution in the `commit` transaction; `num_sources` is `COUNT(DISTINCT item_id)` over it. Fixes fresh single-source pages rendering `num_sources: 0` (the recurrence signal the vector-index sparsity gate relies on). Both changes bump `SYNTHESIZE_WIKI_DAG_VERSION` 5→6 (one re-materialisation epoch).

---

## [0.23.0] — 2026-06-20

### Added

- **Wiki synthesis now skips curator-rejected entities, managed from a Notion "Wiki Pages" database.** Rows marked `Rejected` in Notion are read each tick and their entity_ids dropped at extraction time, so no page is built or updated for them — fail-closed to a last-known-good snapshot (`data/wiki/_index/rejected.json`) on a Notion outage. New `NOTION_WIKI_PAGES_DATA_SOURCE_ID` env var; reuses `NOTION_INTEGRATION_TOKEN`.

### Changed

- **Wiki entity extraction now biases toward specific over generic entities.** The extraction prompt's "significant enough" rule was operationalized with specific-claim / skip-common-knowledge / recurrence tests, and the per-article entity cap lowered 10 → 5.

---

## [0.22.2] — 2026-06-16

### Changed

- **The extract sensor now skips partitions that already have a run in flight.** Previously, a manual Dagster UI re-trigger or a Notion edit mid-processing caused the next sensor tick to launch a second concurrent run for the same page — paying for transcription and LLM extraction twice. The sensor now queries for any queued or running job tagged with the `notion_page_id` and skips it.

---

## [0.22.1] — 2026-06-15

### Changed

- **Podcast rows without a YouTube mirror now flow through to the Whisper handler.** The extract sensor previously filtered only on `Article`, `YouTube`, and a handful of other types — `Podcast` was missing, so rows sat at Status=Fetching indefinitely. Sensor filter now includes `Podcast`.

---

## [0.22.0] — 2026-06-15

### Added

- **Podcast handler for direct audio/video URLs (MP3, M4A, MP4, etc.).** The fetcher now transcribes non-YouTube podcast audio via a Whisper cascade (Groq `whisper-large-v3-turbo` → OpenAI `whisper-1`), then runs the existing transcript structurer on the result. Implementation: `services/fetcher/src/fetcher/handlers/podcast.py` + `extractors/whisper.py`; chain config in `services/fetcher/config/whisper.yaml`.

### Changed

- **`GROQ_API_KEY` now feeds the podcast Whisper cascade in addition to the triage classifier.** No new variable; the existing key gains a second consumer. `ffmpeg` is now a runtime dependency of the fetcher image (`docker/fetcher/Dockerfile`).

---

## [0.21.2] — 2026-06-15

### Changed

- **Article + transcript structurer now actually runs in containerised deploys.** Fetcher Docker image now copies `services/fetcher/config/` and `services/fetcher/prompts/` into `/app/` (they were missing → `_load_chain` silently returned `[]` → cascade emitted misleading "no API keys configured" even when keys were set).
- **Distinct error for missing config vs missing keys.** `_cloud_chain.py` raises `StructurerNotConfigured` (subclass of `StructurerChainFailed`) with separate messages for empty-chain and no-keys; endpoint handlers map both to 503 `STRUCTURER_UNCONFIGURED` via `isinstance` instead of substring matching.

### Removed

- **`FETCHER_OPENAI_API_KEY` and `FETCHER_OLLAMA_API_KEY` env vars.** Fetcher's structurer cascade now reads bare `OPENAI_API_KEY` / `OLLAMA_API_KEY` (shared with orchestrator) via Pydantic `validation_alias` that bypasses the `FETCHER_` prefix for just these two fields. Drop the duplicate vars from prod env.

---

## [0.21.1] — 2026-06-15

### Changed

- **Sync LLM cascade primitive extracted to `workflows.llm_cascade.run_cascade`.** `ContentShapeClassifier` now delegates the 2-tier Groq → OpenAI fall-through to the shared helper, so the next orchestrator-side classifier (queued: transcript structurer Groq swap) can reuse rather than copy-paste. No behaviour change in triage output.

---

## [0.21.0] — 2026-06-15

### Added
- **LLM-primary `content_shape` classifier.** Replaces the deleted YAML rules engine with a `ContentShapeClassifier` Dagster resource that picks one of six shapes (`conference_talk`/`podcast_episode`/`tutorial`/`opinion_essay`/`research_summary`/`unknown`) from existing per-source enrichment (title/description/channel/abstract) via a Groq `llama-3.3-70b-versatile` → OpenAI `gpt-4.1-mini` cascade. URL-deterministic fast-paths bypass the LLM for arXiv URLs (→ `research_summary`) and audio URLs (→ `podcast_episode`). LLM may return `unknown` honestly for ambiguous content — user disambiguates in Notion. Optional `GROQ_API_KEY`; deploys with neither LLM key land non-fast-path URLs as `unknown`. `packages/orchestrators/src/orchestrators/defs/triage_knowledge_queue/content_shape_llm.py`.

### Removed
- **Rules-based `classify_content_shape` + the three YAML lookup tables** (`article_host_rules.yaml`, `youtube_channel_rules.yaml`, `conference_channels.yaml`). The rules silently misclassified multi-shape hosts (Medium, mixed YouTube creators); the LLM-primary classifier reads page-specific enrichment instead of host whitelists.

---

## [0.20.0] — 2026-06-15

### Added
- **Facebook post fetching via RapidAPI.** New `facebook` handler claims `facebook.com` / `fb.com` / `fb.watch` URLs with a two-tier cascade — `facebook-scraper-api4` (URL-keyed, primary) then `facebook-scraper3` (`pfbid`-keyed, fallback). Both share `FETCHER_RAPIDAPI_KEY`; the handler is `STRICT_PAID_TIER=True` so unauthorized fetches surface a Problem instead of falling through to the article handler and returning login-wall junk. `services/fetcher/src/fetcher/handlers/facebook.py`.
- **YouTube captions fallback via RapidAPI youtube-data16.** New paid `rapidapi_captions` tier fires after the free `transcript_api` tier when the latter returns no chunks (YouTube IP-blocks even via the Tailscale SOCKS5 proxy, or no community transcript exists). Extractor maps the upstream `offset` field to `start` so the existing `chunks_to_markdown` formatter consumes the chunks unchanged; structurer runs symmetrically for both tiers via a shared `_finalize_chunks` helper. `services/fetcher/src/fetcher/handlers/youtube.py`, `services/fetcher/src/fetcher/extractors/rapidapi/youtube_captions.py`.

### Changed
- **RapidAPI extractors moved into `extractors/rapidapi/` subpackage** with shared `_client.py` primitives (`build_headers`, `raise_for_status_with_body`, `check_quota`). `rapidapi_medium` → `rapidapi.medium`; new `rapidapi.facebook_api4`, `rapidapi.facebook_scraper3`, and `rapidapi.youtube_captions` compose the same helpers. Pure relocation for medium — error shape unchanged. `services/fetcher/src/fetcher/extractors/rapidapi/`.

---

## [0.19.3] — 2026-06-15

### Changed

- **YouTube transcript fetch now routes through `FETCHER_SOCKS5_URL` when set.** Data-center IPs (Hetzner, AWS) get IP-blocked by YouTube's transcript API; the handler now wires `ctx.socks5_url` into `GenericProxyConfig` so the call egresses via the residential Tailscale path the article handler already uses. Failure detail (exception class) now surfaces in `tier_log.detail` instead of being flattened to generic `"empty"`. `services/fetcher/src/fetcher/handlers/youtube.py`.

---

## [0.19.2] — 2026-06-15

### Changed

- **Triage seeds Notion Name + Description from per-content-type enrichment instead of static HTML.** YouTube titles (JS-rendered) and generic `og:description` boilerplate are now bypassed; the asset resolves oEmbed, Atom API, or article meta depending on content type. `display.py` in `triage_knowledge_queue`; `TRIAGE_KNOWLEDGE_QUEUE_DAG_VERSION` bumped to 2.

---

## [0.19.1] — 2026-06-14

### Added

- **Fetcher `/docs` (Swagger) and `/redoc` now ship as a real API reference.** App metadata (`kp-fetcher` title, summary, multi-line description), four tag groups (Health / Fetch / Normalize / Utilities), descriptive `summary=` on every route, and typed `ProblemResponse` error envelopes declared on all error-returning endpoints (POST `/v1/structure`, `/v1/fetch`, `/v1/fetches`, DELETE `/v1/fetches/{id}`, and `/v1/structure-transcript`). Regression tests pin tags, summaries, and `ProblemResponse` presence so future endpoints can't ship undocumented.

### Changed

- **Pre-existing fetcher test suite tightened against the strict TDD vertical-slicing rules.** Audit of 132 tests across 20 files: 5 deletions, ~25 assertion tightenings (drop pass-throughs, format coupling, mock-kwarg coupling, constant-pinning), 2 splits, 2 characterization rewrites. No runtime behaviour change; net suite count 172 → 170.

---

## [0.19.0] — 2026-06-14

### Added

- **`POST /v1/structure-transcript` — explicit call surface over the cloud transcript structurer.** Same Ollama `gemma4:31b` → `gpt-4.1-mini` fallback chain as the YouTube handler, with a separately namespaced content-keyed cache. Failures surface as 502/503 (no raw fallback) so eval harnesses see them explicitly. Implementation: `fetcher.endpoints.structure_transcript`.
- **YouTube transcripts are now cloud-structured into speaker-attributed paragraphs by default.** Handler runs `transcript_structurer` after fetching auto-captions and falls back to raw on chain failure; raw chunks ride along in `metadata["chunks"]`. Set `FETCHER_YOUTUBE_STRUCTURER_ENABLED=false` to opt out per-deploy.

### Changed

- **`POST /v1/structure` cache now invalidates on prompt, chain config, or hint changes.** Previously only content + primary model were keyed; edits to the prompt file, chain order, or `title`/`author_name`/`content_date` hints silently returned stale markdown. Fix: `fetcher.endpoints.structure._structurer_cache_key` via `_cloud_chain.cache_key_components`.

---

## [0.18.19] — 2026-06-13

### Added

- **Fetcher Swagger UI reachable on the Tailnet at `https://<tailnet>/fetcher/docs`.** Fetcher container starts with `uvicorn --root-path /fetcher` so Swagger generates the right `openapi.json` URL through NA's Caddy reverse proxy; internal callers at `http://fetcher:8000/v1/*` are unchanged.

### Changed

- **Two Dagster pipelines renamed: `extract_complex_contents` → `fetch_extract_queue`, `triage_queued_items` → `triage_knowledge_queue`.** Asset keys, module paths, op names, and `*_DAG_VERSION` constants all move; versions reset to `"1"` (pre-deploy, no materializations to invalidate).
- **Notion Queue checkbox renamed `Use page body as content` → `Use page body`.** Sensor reads only the new name — rename the property in Notion before pulling.

---

## [0.18.18] — 2026-06-13

### Changed

- **Triage now seeds Notion `Name` over auto-default titles.** `"New <db_name> page"` (Notion's default for fresh rows) and `"Untitled"` were treated as user-set names and preserved; new `_is_user_set_name` helper filters them so the fetched page title lands instead.
- **`final_url` renamed to `redirected_url`** on `UrlMeta`, `ArticleSignals`, and the `triaged` materialization metadata key — distinguishes "post-HTTP-redirect URL" from `canonical_url` (dedup key) and `original_url` (raw input). `enrich._build_article` reads both the new and old keys so rows enriched before the rename still parse.
- **`poll_notion_for_extract` self-heals dynamic partitions.** Previously assumed triage had pre-registered every page_id's partition; orphan rows (DAGSTER_HOME reset, prior-deploy carryover) crashed the run launch with `DagsterUnknownPartitionError`. Sensor now registers each polled page_id idempotently in the same `SensorResult`.

---

## [0.18.17] — 2026-06-13

### Changed

- **`ThreeCallOpenAIExtractor` now routes prompts by `content_shape`.** Constructor takes `prompt_sets: dict[str, PromptBundle]` (the `"unknown"` bundle is the generic fallback, required); `extract()` and `bundle_sha256()` both take a `content_shape` kwarg and resolve to the selected bundle (so adding a new shape's prompts doesn't invalidate prior shapes' rows). Cohort `bundle_label` bumps `3call_v1` → `3call_v2_shape_routed` to flag existing rows as stale on the next sensor sweep.
- **`extracted` asset now passes the queue row's `content_shape` to the extractor.** Bundle selection fires per-row using the value triage wrote in Phase 3 (NULL → `"unknown"`). New `prompt_set_shape` column on `extraction_calls` records which PromptBundle actually ran (selected shape, after fallback) so downstream eval queries can group by it. Prod migration: `scripts/migrations/2026-06-13_extraction_calls_prompt_set_shape.sql`. No per-shape prompts registered yet — every row still hits the `unknown` bundle until Phase 6 evaluates lift and per-shape prompt files are added.

---

## [0.18.16] — 2026-06-13

### Added

- **`content_shape` now classified at triage time and stored on `queue_items`.** New rules-only classifier `classify_content_shape` reads cached enrichment + URL → one of `conference_talk` / `podcast_episode` / `tutorial` / `opinion_essay` / `research_summary` / `unknown`. Seed rules in `conference_channels.yaml` / `youtube_channel_rules.yaml` / `article_host_rules.yaml`. `triaged` now depends on `enriched`. Notion property write and extractor prompt routing land in later phases.
- **`Content Shape` Notion SELECT now written by `triaged` and overridable by the user.** Sensor reads user-set value; classifier fires when empty / typo'd (mirrors `Content Type` semantics). `unknown` shape is left blank on Notion so pre-populated overrides aren't stomped. **Manual prep:** add `Content Shape` SELECT with options `conference_talk` / `podcast_episode` / `tutorial` / `opinion_essay` / `research_summary` to the Queue DB before deploy.

---

## [0.18.15] — 2026-06-13

### Added

- **`enriched` triage asset caches per-source URL signals for content-shape classification.** New asset runs in parallel with `triaged` per partition: YouTube oEmbed → channel + title, arXiv Atom API → title + abstract + categories, article → `fetch_url_meta` passthrough; result lands as `enrichment_json` on `queue_items`. Failure-tolerant — any HTTP error collapses to empty signals; never blocks triage. Phase 3 wires the cache into `classify_content_shape`.

---

## [0.18.14] — 2026-06-12

### Added

- **`queue.db` ready for content-shape extractor routing.** New `content_shape` + `enrichment_json` columns on `queue_items`, plus `upsert_enriched` / `get_content_shape` helpers in `domains.queue_store.sources`. Schema-only landing — classifier + asset wiring follow in later phases. Prod migration: `scripts/migrations/2026-06-12_queue_items_content_shape.sql`.

---

## [0.18.13] — 2026-06-12

### Changed

- **Re-triaging a Notion row now invalidates the prior cohort's fetched + extracted state.** `queue_store.upsert_triaged` ON CONFLICT clears `raw_content` / `fetched_*` / `extracted_*` cohort fields and deletes the row's `extraction_calls`. Previously a re-queue against a row with cached `raw_content` short-circuited the `fetched` cache check, running extraction on stale content — the symptom that forced a manual `DELETE FROM queue_items` after the PR #109 Medium-handler fix.
- **`published` now overwrites Notion `Name` with `topic_card.extracted_title`** alongside the existing `Description` write — the LLM's title is materially sharper than the trafilatura HTML meta triage seeded ("Welcome to Medium" → "Why I Quit My Job at Google"). `NotionQueueResource.update_status` gains an optional `name` parameter with the same strip-and-skip-on-empty discipline as `description`.

---

## [0.18.12] — 2026-06-12

### Changed

- **Medium URLs now route through the medium handler in prod.** `medium_domains.yaml` moved into the package at `src/fetcher/data/`, loaded via a `Path(__file__)`-relative default — ships with the wheel, no Dockerfile data COPY or env var. `_load_domains` fails fast on missing/empty file so the next regression surfaces at fetcher startup, not silently.
- **Fetcher `tier_log` now carries per-tier diagnostic detail.** `TierLogEntry` gains `duration_ms` / `floor` / `error_kind` / `detail`; handlers populate `RawTierResult.detail` with 4xx body slices, exception text, and upstream reasons (jina 401, curl_cffi failures, tavily/rapidapi/arxiv errors). Cache reads stay back-compat via `.get(...)` defaults.
- **Fetched-asset metadata now includes `canonical_url`** — the cross-repo `kp_queue_cache` lookup key was previously invisible in the Dagster materialization view.
- **`canonicalize_url` renamed to `normalize_url`** in `triage_queued_items/classify.py`, matching NA's function name so the byte-for-byte parity contract is self-documenting. Disambiguates from the fetcher service's HEAD-follow `canonicalize()`. DB column `queue_items.canonical_url` keeps its name.

---

## [0.18.11] — 2026-06-11

### Changed

- **arXiv URLs canonicalise to `/abs/<id>` at triage time.** `canonicalize_url` previously passed arXiv URLs through unchanged, leaving every arXiv row's `queue_items.canonical_url` mismatched against NA's `normalize_url` — silent NA→kp `kp_queue_cache` miss since arXiv ingestion existed. Now collapses `abs`/`pdf`/`html`/bare-ID forms to `<scheme>://<netloc>/abs/<id>` (version stripped), mirroring NA's existing abs/pdf canonicalisation. The `html/` extension is one-sided pending a coordinated NA-side change to `is_arxiv_url`.

---

## [0.18.10] — 2026-06-11

### Changed

- **arXiv URLs at `arxiv.org/html/<id>` now route through the arxiv fetcher handler.** The handler previously matched only `abs/` and `pdf/` path prefixes; `html/`-form URLs fell through to the generic article handler and surfaced as ``no handler matches URL``. The handler still resolves the canonical PDF via the metadata API, so the html surface is purely a new entry point — same downstream extraction.

---

## [0.18.9] — 2026-06-11

### Changed

- **Backup pipeline's `storage_capacity` asset now surfaces rclone's stderr in the Dagster failure.** Previously `subprocess.run(..., capture_output=True, check=True)` swallowed stderr — `rclone about gdrive:` failures (expired OAuth, missing remote, network) appeared only as `CalledProcessError` with no diagnostic. Now raises `dg.Failure` with stderr in description + metadata (`remote`, `exit_code`, `stderr`).

---

## [0.18.8] — 2026-06-11

### Changed

- **Three env vars must be renamed in your deploy `.env`.** `BACKUP_SOURCE_DIR` → `BACKUP_SRC_DIR`, `BACKUP_DIR` → `BACKUP_DST_DIR` (backup pipeline), and `FETCHER_DEFAULT_TIMEOUT_S` → `FETCHER_UPSTREAM_TIMEOUT_S` (fetcher service). Old names will cause a failed run init or silent misconfiguration.
- **Four env vars can be removed from your deploy `.env`.** `OPENAI_EMBEDDING_MODEL`, `OPENAI_EMBEDDING_DIMS`, `VECTOR_STORE_MAX_PER_TICK`, and `WIKI_MAX_PER_TICK` are now code constants in `def_config.py`; they are coupled to existing Chroma vectors and do not vary per deploy.

---

## [0.18.7] — 2026-06-11

### Changed

- **Extract pipeline guard failures now include a clickable Notion page URL.** Both failure paths — missing `queue_items` row and missing Content Type — surface the Notion URL as a `dg.MetadataValue.url` link in the Dagster UI event and as plain text in container logs and the Notion row's Error column, cutting triage time from "which partition failed?" to one click.
- **Poll sensor logs `queue_db_id` on every tick.** The configured Notion database ID appears in Dozzle logs at the start of each sensor evaluation, making `NOTION_QUEUE_DB_ID` misroutes visible immediately rather than hours into a silent incident.

---

## [0.18.6] — 2026-06-10

### Added

- **Podcast audio URLs are now auto-substituted to YouTube at triage time.** When a queued item resolves to an audio file (`.mp3`, `.m4a`, etc.) and the show is in `podcast_youtube_map.yaml`, the triage asset fetches the show's YouTube playlist feed, fuzzy-matches the episode title, and replaces the canonical URL with the matched YouTube URL — routing the item through the YouTube fetcher for a free transcript instead of Whisper.

### Changed

- **`classify_content_type` now emits `"Podcast"` for direct audio file URLs.** Previously those fell through to `"Article"`; they now get a distinct type so the substitution step can intercept them.

---

## [0.18.5] — 2026-06-10

### Added

- **`POST /v1/structure` on the fetcher service** turns noisy user-pasted article bodies into clean markdown via a trafilatura → conservative passthrough → cloud LLM cascade (Ollama Cloud primary, OpenAI fallback). Chain config: `services/fetcher/config/structurer.yaml`; prompt: `services/fetcher/prompts/structure_v1.md`; new envs `FETCHER_OPENAI_API_KEY` / `FETCHER_OLLAMA_API_KEY` (at least one required for the cloud stage).
- **Ticking Notion's `Use page body as content` checkbox routes the row's pasted body through `/v1/structure` instead of fetching the URL.** Sensor reads the checkbox, converts block children via `notion_blocks.blocks_to_markdown`, and threads the result through new `queue_items.raw_content_override` column. Requires the one-time `scripts/migrations/2026-06-10_queue_items_raw_content_override.sql` against prod queue.db before deploy.

### Changed

- **Triage now routes every supported URL to `Status=Fetching` — the Tier A / Tier B split is gone.** Article and Other rows that previously stopped at `Status=Ready` for NA-at-engagement now flow through `extract_complex_contents.fetched` against the fetcher's article handler (catch-all). `SUPPORTED_CONTENT_TYPES` widened to `{YouTube, arXiv, Article, Other}`; `is_tier_a()` and `_TIER_A` removed.
- **`FetcherResource` no longer tunnels orchestrator → fetcher calls through `HTTP(S)_PROXY`.** `httpx.Client(trust_env=False)` keeps the internal service-to-service call off the upstream proxy meant for the fetcher's own egress.

---

## [0.18.4] — 2026-06-10

### Added

- **Medium articles now fetch via a dedicated handler.** Jina free tier, then mediumapi.com RapidAPI paywall bypass under optional `FETCHER_RAPIDAPI_KEY`. Domain set in `services/fetcher/config/medium_domains.yaml`; path overridable via `FETCHER_MEDIUM_DOMAINS_PATH`.
- **Generic PDF URLs now route to a dedicated `pdf` handler.** Free tier: pymupdf4llm with a 50 MB download cap. Paid tier: LlamaParse via `FETCHER_LLAMA_PARSE_TIER_PDF` (defaults to `fast`). arXiv PDFs still go to the arxiv handler.
- **Article handler gains a paid Tavily Extract tier.** Runs when Jina and curl_cffi+trafilatura both fail validation. `FETCHER_TAVILY_API_KEY` is optional; tier is unreachable when unset.

### Changed

- **Fetcher returns 502 `UPSTREAM_FAILURE` when no tier produces clean content.** Article and Medium tiers run output through `is_valid_content` + `is_likely_truncated` (`services/fetcher/src/fetcher/validator.py`) — paywall fragments, JS walls, Cloudflare challenges, and truncated bodies no longer surface as silent 200s.
- **Jina requests now carry `X-Return-Format: markdown` and `X-Timeout: 20`** so the response shape is explicit and Jina enforces its own timeout below the httpx client's.
- **`CLAUDE.md` @-imports now resolve again** — directory renamed `personal-knowledge-os/` → `knowledge-os/` upstream (data-context-builder PR #47). Updated absolute paths plus a stray test-fixture tag in `tests/domains/queue_store/test_sources.py`.

---

## [0.18.3] — 2026-06-09

### Changed

- **Fetcher service wire vocabulary now uses `kind`.** `/v1/fetch` response field `source_type` → `kind`; `/healthz` field `registered_sources` → `registered_kinds`; error code `UNSUPPORTED_SOURCE` → `UNSUPPORTED_KIND`. Internal directory `services/fetcher/src/fetcher/sources/` → `handlers/` reflects that each module handles a URL kind. Cache schema column unchanged.

---

## [0.18.2] — 2026-06-09

### Changed

- **Fetcher service: extraction primitives moved from `services/fetcher/src/fetcher/parsers/` to `extractors/`.** Internal rename; the directory mixes local parsers (`trafilatura`, `pymupdf`) with remote-API callers (`llamaparse`, `oembed`, `youtube_transcript`), and the new name describes both honestly. No consumer impact — the service is HTTP-only.

---

## [0.18.1] — 2026-06-09

### Changed

- **`extract_complex_contents/fetched` now delegates all URL→markdown work to the fetcher service.** `FetcherResource` is a thin httpx client to `POST /v1/fetch`; per-tier in-process fetchers (curl_cffi+trafilatura, pymupdf+LlamaParse, youtube-transcript-api) are removed. RFC 7807 `retryable` flows into Dagster `allow_retries`, so transient blips re-queue and permanent failures (bad URL, unsupported source) fail fast.

- **Env contract updated for `extract_complex_contents`.** Three new required vars: `FETCHER_URL`, `FETCHER_TIMEOUT_S`, `FETCHER_ALLOW_PAID`. Removes `PI_SOCKS5_URL`, `EXTRACT_QUEUE_IMPERSONATE_PROFILE`, `YOUTUBE_PROXY_URL`, `LLAMA_CLOUD_API_KEY`, `LLAMA_PARSE_TIER` — those moved into the fetcher service in 0.18.0. `EXTRACT_COMPLEX_CONTENTS_DAG_VERSION` bumped 2→3; previously-materialized `fetched` partitions show stale on first deploy and refill on the next scheduled tick.

- **Extraction floor lowered from 2000 → 500 chars.** The fetcher cascade can return sub-floor content via its `best_result` fallback; 500 rejects degenerate fetches while letting short legitimate YouTube clips through.

- **`fetched` Dagster UI compute_kind changed from `python` to `http`.** Matches the system-not-tool convention used by `extracted` (`openai`) and `published` (`notion`).

- **Fetcher service now emits its own INFO logs.** `logging.basicConfig(force=True)` in `create_app()` overrides uvicorn's pre-installed root logger config so `fetcher.*` namespace lines surface.

### Added

- **`make dev` starts the full laptop dev stack in one shot** — data services, fetcher service, and Dagster UI. `poe fetcher-dev` and `make fetcher-dev` also available when only the fetcher needs to be run independently.

### Removed

- **`extract_complex_contents/fetchers/` package deleted** (`article.py`, `arxiv.py`, `youtube.py`, `result.py`). `curl-cffi`, `youtube-transcript-api`, `pysocks`, and `arxiv` dropped from `packages/orchestrators` dependencies.

---

## [0.18.0] — 2026-06-09

### Added

- **Fetcher service** at `services/fetcher/` — URL → markdown for KP and NA, replacing the per-pipeline fetchers. Three sources today (article via Jina → curl_cffi+trafilatura; arxiv via pymupdf4llm → LlamaParse agentic_plus, strict; youtube via transcript-api with oEmbed header). Endpoints: `POST /v1/fetch` (sync, ETag / 304), `POST/GET/DELETE /v1/fetches` (async batch with real in-process cancellation), `GET /v1/canonicalize`.

- **`domains.fetches_store.sources`** owns all fetcher SQLite state. New named ops (`cache_lookup/upsert`, `insert_job`, `update_job`, `get_job`, `get_job_status`, `canonicalize_lookup/upsert`, `mark_orphans_failed`) match the `queue_store`/`raw_store` convention — `_connect` stays private, callers never see SQL or connections.

- **Free-first tier cascade.** Each source declares ordered free → paid tiers; cascade walks free tiers first, escalates to paid only when `allow_paid=true` AND the quality floor isn't met. Paid escalations (LlamaParse `agentic_plus`) gated behind explicit caller intent.

- **`kos-network` external Docker network** shared with sibling stacks. Fetcher reachable as `http://fetcher:8000` from KP containers and `http://kp-fetcher:8000` from other KOS stacks. `scripts/deploy-hcloud.sh setup` creates the network on first deploy.

- **Single-worker invariant.** `uvicorn --workers 1` is load-bearing: per-source `asyncio.Semaphore`, per-URL `asyncio.Lock`, and `task_registry: dict[job_id, asyncio.Task]` are correctness primitives. README banner documents the failure modes if this is violated.

- **RFC 7807 error contract** at `endpoints/errors.py` with `retryable` field. Exception hierarchy (`BadUrl`, `UnsupportedSource`, `UpstreamFailure`, `UpstreamTimeout`, `RateLimited`) split across `errors.py` (domain) and `problems.py` (pure body factory, no FastAPI) so workers and cache can persist the same problem shape into SQLite.

- **Soft-404 guard on article tiers.** Jina Reader wraps upstream 4xx/5xx in HTTP 200 with a `"Warning: Target URL returned error"` marker; the guard now demotes these to empty content. curl_cffi short-circuits on HTTP status ≥ 400 before invoking trafilatura. Prevents error pages from being cached as article text.

### Changed

- **`FETCHER_JINA_API_KEY` is now optional.** Jina Reader's free tier works without auth at lower rate limits; the key only unlocks the paid quota. `Authorization` header is set only when a key is configured. Required envs that remain: `FETCHER_SOCKS5_URL`, `FETCHER_LLAMA_PARSE_API_KEY`.

---

## [0.17.3] — 2026-06-08

### Changed

- Use TRUNCATE policy after queue db database write to ensure checkpoints are merged into main db. Allow reader can read full data. 

## [0.17.2] — 2026-06-06

### Changed

- **`TopicCardScorer` scores `extracted_title` via embedding similarity, not exact match.** First real benchmark run revealed exact-match on free-text titles was pure noise — LLMs almost never reproduce a hand-written reference verbatim, dragging `__overall__` down by ~0.13. `ExactMatchJudge` remains for future tag-like fields.

---

## [0.17.1] — 2026-06-06

### Added

- **`evals.extraction` harness** consuming the `evals.core` substrate — `make_three_call_variant`, `TopicCardScorer`, `run_variant`s, `run_benchmark`.
- **`eval-extraction` CLI** with `--dry-run` pre-flight cost estimate; live mode redirects to the workbench notebooks until a variant registry lands.
- **Three workbench notebooks** (`ab_topic_card` / `ab_narrative` / `ab_followups`) plus a 9-cell `_template`, all jupytext py:percent paired.
- **`poe nb-sync` and `poe nb-run`** for jupytext + papermill workflows; `poe jupyter` now roots at `packages/evals/notebooks/`.
- **`datasets/extraction_eval.jsonl`** — 15-row hand-curated synthetic fixture set (5 per content type) with measurement-floor caveat documented in `datasets/README.md`.

### Changed

- **Extractor variants now work inside Jupyter kernels.** `make_three_call_variant` thread-hops `ThreeCallOpenAIExtractor.extract()` when a running event loop is detected, avoiding the prior `asyncio.run()` `RuntimeError`.

---

## [0.17.0] — 2026-06-06

### Added

- **`evals.core` substrate** — pure-function primitives composing into per-pipeline harnesses in Steps 3+. Variant identity hashing over `(config, provenance)`; schema-versioned JSONL fixtures (`SchemaVersionMismatch` on drift); JSON-safe `snapshot()` with sentinels for LangGraph state.
- **Run persistence** at `data/eval_runs/{kind}/{target}/{version}/{run_id}/run.json` — workbench (30d retention) vs benchmark (indefinite) split via `kind`.
- **Four judges** with injected callables — `ExactMatchJudge`, `EmbeddingSimilarityJudge`, `LLMJudge`, plus `JudgeProtocol`. No provider deps in the substrate.
- **`CostBudget` + `BudgetExceededError`** — pre-launch spend gate; harness aborts before $$.
- **Field-level `DiffReport`** with text + HTML renderers for variant comparison.
- **Eval composition patterns documented** in `packages/evals/README.md` — adding a content type, upgrading a prompt, composing a workflow graph, golden-regression migration.

---

## [0.16.0] — 2026-06-06

### Changed

- **Extractor classes now live in `workflows.extraction`.** `ThreeCallOpenAIExtractor`, `ExtractorProtocol`, and `ExtractionUsage` move out of `orchestrators`; production import path updated.
- **Extraction prompts moved to repo-root `prompts/extraction/`.** `KP_PROMPTS_ROOT` env var overrides the default root for evals and tests.

### Removed

- **`SingleShotOpenAIExtractor` deleted** — unused since the three-call cutover.

---

## [0.15.5] — 2026-06-06

### Changed

- **Notion `Error` column shows the actual step failure reason** instead of `Steps failed: [...]`. New shared helper `defs/shared/run_failure.py` reads the terminal `dg.Failure(description=...)`.
- **Extract retries are now transient-only.** `FetchResult.transient` (set by arXiv on 5xx/connection, YouTube on IP/request blocks) gates `allow_retries`; asset-level `RetryPolicy(max_retries=1, delay=120)` replaces the in-fetcher 60s tenacity loop (now 15s).

---

## [0.15.4] — 2026-06-04

### Changed

- **Triage duplicate-detection Error in Notion is now clickable.** Was: `"Duplicate of <bare-uuid>"`. Now: `"Duplicate of <Name>" — <canonical_url>`, where `<Name>` links to the original Notion page and `<canonical_url>` is itself a hyperlink. Adds `NotionQueueResource.get_page_name()` (one extra Notion read per duplicate) and reshapes `update_status_skipped(page_id, segments)` to accept structured rich_text tuples instead of a plain string.

---

## [0.15.3] — 2026-06-04

### Changed

- **Asset graph now shows the stores each pipeline reads.** Added `research_store`, `queue_store`, and `notion_queue` `AssetSpec` anchors in `upstream_sources.py`. `snapshot_research` / `snapshot_queue` (backup) and `triage_queued_items/triaged` declare `deps=` on the relevant anchors. Existing `sessions` anchor renamed to `session_store` for SQLite-store naming consistency (`raw_store` / `queue_store` / `session_store`). Pure graph-rendering change — no compute logic shifts.

---

## [0.15.2] — 2026-06-03

### Changed

- **arXiv fetcher now survives transient upstream overload.** Retry budget extended 15s → 60s; `ConnectionError` added alongside `HTTPError` so 503/429 spells from `export.arxiv.org` no longer surface as run failures.
- **Per-pipeline concurrency keys now actually enforce serialisation.** Added `concurrency.pools` (`granularity: op`, `default_limit: 1`) to `dagster.yaml`; previously the existing op-tag keys were metadata-only and parallel runs could race upstream APIs.

---

## [0.15.1] — 2026-06-03

### Changed

- **Triage dedupes Notion captures by `canonical_url`** — a second capture of an already-queued URL → `Status=Skipped`, `Error="Duplicate of <other_page_id>"`; no queue.db pollution. **Deploy:** add a `Skipped` option to the Notion Queue's Status property before merge.

---

## [0.15.0] — 2026-06-03

### Changed

- **Extraction is now three focused LLM calls per item** (narrative markdown + structured `TopicCard` + structured `Followups`) replacing the single monolithic call. Calls 2+3 fire in parallel and hit OpenAI's prefix cache within sub-seconds of call 1. Storage moves to an `extraction_calls` table (one row per call, mirrors NA's `core_llm_calls` shape); the legacy single-shot columns are dropped.
- **Prompt labels moved from env vars to code constants** — `EXTRACT_QUEUE_PROMPT_LABEL_{ARTICLE,YOUTUBE,ARXIV}` removed; no replacement env vars needed. **Deploy:** remove those three vars from `.env` / `.env.deploy`, run `scripts/migrate_extraction_to_calls_table.py` on prod `queue.db`, then re-trigger `extract_complex_contents/extracted` on the ~7 already-extracted partitions (~$0.20–0.50 OpenAI spend).

---

## [0.14.9] — 2026-06-03

### Changed

- **Notion Queue `Canonical URL` flipped URL-type → Text-type.** With two URL properties on the DB, Web Clipper / Save-to-Notion silently routed mobile captures to `Canonical URL` instead of `URL`, stranding rows at `Status=Queued`. Text-typing leaves only one URL-type property → unambiguous capture. **Deploy:** flip property type in Notion UI at merge. (`shared/queue_resources.py`)
- **Triage backfills `Added At` from `created_time` when missing.** Mobile captures often omit it; sensor now writes `created_time` through; existing values preserved. (`triage_queued_items/sensors.py`, `assets.py`)

---

## [0.14.8] — 2026-06-03

### Changed

- **`canonical_url` matches newsletter-assistant's `normalize_url`** — kp was emitting a different shape than NA's lookup form, so NA's `kp_queue_cache` tier silently missed on the `www.` / `youtu.be` / `?si=` axes. Now mirrors NA: keep only `v=` on `youtube.com`/`m.`/`music.`; strip query+fragment everywhere else. (`triage_queued_items/classify.py`)
- **Triage writes `Canonical URL` to Notion** — new URL-type property on the Queue DB, set in triage's first properties update. (`shared/queue_resources.py`, `triage_queued_items/assets.py`)
- **Deploy action required:** backfill prod `queue.db` on Hetzner with the new canonical shape (commands in PR #75 body). Existing rows have stale `canonical_url`; new triage runs are correct without intervention.

---

## [0.14.7] — 2026-06-03

### Changed

- **LlamaParse tier promoted to required `LLAMA_PARSE_TIER` env var** — was hardcoded as `agentic_plus` (LlamaCloud's most expensive tier), forcing dev iteration to burn prod-grade credits. Dev now sets `LLAMA_PARSE_TIER=fast` (layout-only, ~100× cheaper); prod sets `LLAMA_PARSE_TIER=agentic_plus`. Field renamed `llama_parse_tier_arxiv` → `llama_parse_tier`. **Deploy action required:** set the var in `.env` and `.env.deploy` before next deploy — unset → run init fails fast. (`extract_complex_contents/resources.py`, `.env.example`)

---

## [0.14.6] — 2026-06-02

### Changed

- queue.db access module moved from `domains.raw_store.queue` to `domains.queue_store.sources` — one storage backend per `domains/<store>/` module, matching the rest of the per-store layout. Public function signatures and the `queue.db` schema are unchanged.

---

## [0.14.5] — 2026-06-02

### Changed

- **YouTube transcript fetcher now accepts `socks5://` proxy URLs** — `pysocks` added to `knowledge-orchestrators` deps (`packages/orchestrators/pyproject.toml`). Previously, setting `YOUTUBE_PROXY_URL=socks5://…` raised `InvalidSchema: Missing dependencies for SOCKS support` in `requests`; now it routes correctly. (`curl_cffi`-based article fetcher is unaffected — libcurl bundles its own SOCKS support.)

---

## [0.14.4] — 2026-06-02

### Added

- **Daily backup of `queue.db`** (kp's triage + extract queue) — disk loss no longer means re-classifying and re-extracting every queued item. `queue.db` is sourced from kp's own `data/` directory rather than `BACKUP_SOURCE_DIR`. (`backup_readings/assets.py`, `backup_readings/checks.py`)

---

## [0.14.3] — 2026-06-02

### Changed

- **Extract pipeline now writes `core_mechanism` to Notion's Description property for Tier A items** — after a successful extraction, the published asset overwrites the triage-time HTML meta with the model-extracted `core_mechanism` field. (`extract_complex_contents/assets.py`, `shared/queue_resources.py`)

---

## [0.14.2] — 2026-06-02

### Changed

- **Notion `Status` property migrated from `select` → native `status` type** — queue reads and writes now use `{"status": {"name": …}}` instead of `{"select": {"name": …}}`, matching the Notion schema after its native-status migration. Without this, the pipeline would silently stop processing the queue (triage sensor finds zero rows, extract sensor finds zero rows). Both `query_for_triage` and `query_for_extract` filters updated; `write_triaged`, `update_status`, and `update_status_failed` all write the new type. (`shared/queue_resources.py`)
- **Whitespace stripped from Notion title and description writes** — incoming HTML metadata may carry trailing newlines or padding; both fields are now `.strip()`-ed before writing to Notion. A value that strips to empty is treated as absent (no blank overwrite). (`NotionQueueResource.write_triaged`)

---

## [0.14.1] — 2026-06-02

### Added

- **URL enrichment at triage** — `triaged` now follows redirects and extracts page title + short description (≤ 200 chars) from HTML head before classifying. Notion's `Name` is seeded from the fetched title when the user left it blank; `Description` is always written when one was found. Implementation: `triage_queued_items/url_meta.py` (`fetch_url_meta`, backed by `httpx` + `trafilatura`; never raises — empty meta on any network or parse failure).

### Changed

- **Triage Notion write now includes `Name` + `Description` fields** — `TriageNotionResource.write_triaged` accepts optional `name` and `description`; name is skipped when the user already set one, description is always overwritten. `final_url`, `fetched_title`, and `fetched_description` added to asset materialization metadata.

---

## [0.14.0] — 2026-06-01

### Added

- **YouTube transcript fetcher** — `youtube-transcript-api` + oEmbed; optional `YOUTUBE_PROXY_URL` for IP-blocked hosts.
- **arXiv fetcher** — `arxiv` PyPI + LlamaParse on `agentic_plus` tier (hard-fail). New env `LLAMA_CLOUD_API_KEY`.
- **Per-type prompt labels** — `EXTRACT_QUEUE_PROMPT_LABEL_{ARTICLE,YOUTUBE,ARXIV}` replace singular `EXTRACT_QUEUE_PROMPT_LABEL`.
- **Head + tail content previews** on `fetched` + `extracted` (500+500 chars) in MaterializeResult metadata.
- **Per-pipeline run-failure sensors** — `mark_notion_failed_on_{triage,extract}` write `Status=Failed` + error to Notion.

### Changed

- **Pipeline split** — single `extract_queued_items` → `triage_queued_items` + `extract_complex_contents` coordinated via Notion `Status` + `Content Type` ("Notion-as-bus"). Shared `queue_items` dynamic partition in `defs/shared/partitions.py`.
- **Triage: single `triaged` asset** (was `classified` + `routed`). Notion-set `Content Type` overrides URL classifier; typo/empty → classifier fallback. `Name` passthrough as metadata; triage never writes it.
- **Extract: 6 branched assets → 3** (`fetched` → `extracted` → `published`). Per-type dispatch in `FetcherResource` + `ExtractorRegistry`; `published` isolates Notion write so a Notion hiccup doesn't re-spend OpenAI.
- **Typed `dg.Config` for asset inputs** (was stringly-typed tags). Pydantic validates at launch; manual UI launches use the Launchpad config form.
- **`queue_items` schema** — single `extraction_payload` JSON column + `canonical_url` + `content_type`. Idempotent `ALTER TABLE` upgrade for existing DBs. `get_queue_extraction()` consumer API preserved.
- **Extraction via OpenAI + `ExtractorRegistry` strategy** (`extractors/` subfolder mirrors `fetchers/`). v1: `SingleShotOpenAIExtractor`; future per-type swaps drop in as one file + one registry line. `anthropic` dep dropped, `openai` added.
- **Sensor names symmetric** — `poll_notion_for_<stage>` + `mark_notion_failed_on_<stage>` across both pipelines.

### Removed

- **`TitleFetcherResource`** — `<title>` tag was SEO junk on most sites; downstream extract LLM / NA-at-engagement fills `Name` from real content.
- **`pymupdf4llm`** dep — arXiv uses LlamaParse exclusively.

---

## [0.13.0] — 2026-06-01

### Added

- **`extract_queued_items` pipeline** — turns Notion-captured URLs into extracted Topic Cards. Sensor polls Notion queue every 15 min (matches `Status=Queued` and rows with empty Status — mobile Share Sheet bypasses Notion templates on the free tier); per URL: Jina → curl-cffi/Pi SOCKS5 fallback → Anthropic Topic Card → flips Notion `Status=Ready`. Failures mirror back as `Status=Failed`. New `orchestrators` deps: `notion-client`, `curl-cffi`, `trafilatura`, `anthropic`. See `packages/orchestrators/src/orchestrators/defs/extract_queued_items/README.md` for envs + DB setup.

- **`queue_items` SQLite table + `get_queue_extraction()` consumer API** (`data/queue.db`, `domains.raw_store.queue`). Notion holds lifecycle status + URL only; all extracted content stays kp-local per the 2026-05-27 privacy decision. `get_queue_extraction(notion_page_id=...)` is the cross-repo read path newsletter-assistant uses on engagement.

---

## [0.12.4] — 2026-05-17

### Added

- **OS-wide framing `@`-imported into `CLAUDE.md`** (`~/GitHub/data-context-builder/documents/personal-knowledge-os/framing.md`). Previously only the local trajectory was imported — sessions now see the canonical exit-ramps + decision rubric alongside this repo's trajectory.

### Changed

- **Per-repo trajectory `@`-import now points at the personal-knowledge-OS hub** (`~/GitHub/data-context-builder/documents/personal-knowledge-os/trajectories/knowledge-pipeline.md`) rather than the local `docs/concept/personal-knowledge-os.md`, which has been removed. Both the OS-wide framing and this repo's trajectory now live in the hub. Trajectory updates land as hub PRs, derived from this `CHANGELOG.md`.

---

## [0.12.3] — 2026-05-15

### Changed

- **`populate_vector_store` schedule now fires successfully on HH:30 ticks.** Cron is `*/30 * * * *` but assets were partitioned hourly — HH:30 ticks died with `DagsterUnknownPartitionError`. Partitions are now a 30-min `TimeWindowPartitionsDefinition` aligned to the cron (`def_config.py`); `POPULATE_VECTOR_STORE_DAG_VERSION` → `"2"`.

---

## [0.12.2] — 2026-05-14

### Changed

- **`dagster-code` image now creates `/opt/dagster/home/storage` with `dagster` ownership at build time.** Pipelines failed in production with `PermissionError: [Errno 13] Permission denied: '/opt/dagster'` because the daemon/webserver send their `DAGSTER_HOME`-derived fs_io_manager path over gRPC to dagster-code, whose image never created `/opt/dagster`. The path now exists in the user-code image with the right owner; asset outputs persist successfully. Container-layer storage (not a volume) — IO outputs are re-materializable on rebuild, matching default fs_io_manager semantics.

- **`.env.example` no longer hard-codes `APP_UID=1001` / `APP_GID=1001`.** The previous comment claimed `1001` was "fine for first-time setup," but Hetzner / stock Ubuntu hosts have their first non-root user at uid 1000, causing bind-mount ownership mismatches at deploy time. New placeholder is `1000` and the comment is now a MUST-do instruction to verify with `id -u` / `id -g` on the deploy host before the first `docker compose --profile app build`.

---

## [0.12.1] — 2026-05-14

### Changed

- **`wiki/aliases_index` now self-maps every page entity_id**, even for entities with zero rows in `wiki.aliases`. Previously such entities were absent from `data/wiki/_index/aliases.json` — the consumer agent's lookup would silently return `None` and fall through to vector recall, masking the wiki page. Fix: a new loop over `wiki.pages` registers `entity_id → entity_id` before the alias and canonical-name loops; idempotent under the existing collision check.

---

## [0.12.0] — 2026-05-14

### Added

- **Three new YAML frontmatter fields on every wiki page** — `summary` (one-sentence LLM-generated description, shape-word-free), `aliases` (list of alternate entity names sourced from `wiki.aliases` in Postgres, not from the LLM), and `num_sources` (integer count of distinct source content_ids). Downstream consumers (e.g. newsletter-assistant) can now "peek before fetch" without loading the full page body. Fields written in `domains/wiki/io.py`; aliases + source count fetched via two new helpers in `domains/wiki/state.py`.

- **Alias index sidecar at `data/wiki/_index/aliases.json`** — flat `{lowercased_alias_or_canonical_title → entity_id}` map rebuilt at the end of every `synthesize_wiki` tick by the new `wiki/aliases_index` Dagster asset. Atomic write (tmp + rename); skipped when byte-identical; raises on collision. Enables O(1) entity resolution for sibling agents.

### Changed

- **LLM page-output parsing is now whitelist-only** — only documented fields are accepted; hallucinated `aliases` / `num_sources` from the model are silently dropped. Falls back to first sentence of body when `summary` is missing or empty. Logic lives in `workflows/wiki_synthesis/parsing.py`.

- **`SYNTHESIZE_WIKI_DAG_VERSION` bumped 3 → 4** — new `wiki/aliases_index` asset changes DAG topology.

---

## [0.11.3] — 2026-05-14

### Changed

- **Heading-aware embeddings for markdown content.** Markdown-chunked items (raw_store, notes, research) now embed with their heading breadcrumb prepended (e.g. `"Introduction > Setup\n\nactual chunk text..."`), so retrieval better ranks chunks within their containing section. The stored `document` field in Chroma stays unchanged — only the embedded vector encodes the breadcrumb. Heading metadata also lands in Chroma as `heading_path` for all sources, including time-range strings for `turn_grouping` (sessions). No re-embed needed for downstream consumers; existing chunks remain searchable, new ingests include the new field.

---

## [0.11.2] — 2026-05-14

### Added

- **Chroma service in docker-compose** as `chroma` (image `chromadb/chroma:1.5.5`, loopback-bound port `127.0.0.1:8000`, named `chroma_data` volume). The `populate_vector_store` pipeline now has a server to point at on the deployed host; `dagster-code`'s `environment:` overrides `CHROMA_HOST=chroma` so the compose-internal network name takes precedence over `.env`. Pipeline schedule remains paused — turning it on is the next phase.

- **Compose profiles for local-dev split.** Services are now grouped into `data` (postgres + chroma) and `app` (dagster-code + dagster-webserver + dagster-daemon). Data services hold both profiles so `--profile app` resolves `depends_on` links and `--profile data` starts them standalone. `poe dagster-dev` now owns the data-services lifecycle: it brings postgres + chroma up at start and tears them down on exit (Ctrl+C, crash, normal end) via a bash EXIT/INT/TERM trap. Production deploy script (`scripts/deploy-hcloud.sh`) now invokes `docker compose --profile app`. **Bare `docker compose up` now starts nothing** — every service is profile-gated; use `--profile app` (full stack) or `--profile data` (deps only).

---

## [0.11.1] — 2026-05-14

### Changed

- **Code-location module path shortened.** Pipelines now live directly under `orchestrators.defs.<name>` (was `orchestrators.defs.pipelines.<name>`); the production Docker CMD and `poe` tasks load `orchestrators.definitions` as the single code-location entry point (was `orchestrators.defs.pipelines.definitions`). Pure module-path refactor — asset names, partitioning, schedules, and resources unchanged.

---

## [0.11.0] — 2026-05-13

### Added

- **`populate_vector_store` pipeline embeds raw_store, notes, sessions, and research into ChromaDB** via OpenAI `text-embedding-3-small` (1536 dims). Four collections are written: `contents`, `notes`, `conversations`, `research_documents`. Metadata per chunk includes `content_id`, `chunk_index`, `_embedding_model`, `_embedding_dims`, plus optional fields (`title`, `author`, `content_date`, `url`, `started_at`, `source_ref`). Pipeline launches paused (`STOPPED`) — manual trigger only for now.

- **New required env vars `CHROMA_HOST` and `CHROMA_PORT`** to connect the pipeline to ChromaDB; unset → run init fails fast. Optional tuning knobs: `OPENAI_EMBEDDING_MODEL` (default `text-embedding-3-small`), `OPENAI_EMBEDDING_DIMS` (default `1536`), `VECTOR_STORE_MAX_PER_TICK` (default `50`), `VECTOR_STORE_INGEST_CONCURRENCY` (default `4`). All documented in `.env.example`.

---

## [0.10.0] — 2026-05-12

### Changed

- **`VectorStoreResource` is now HTTP-only.** The `chroma_path` config field is removed; replace with `chroma_host` (required) and `chroma_port` (default `8000`). `get_collection` no longer accepts an `embedding_model` argument — pass only `name`. Any config YAML or resource instantiation referencing `chroma_path` or `embedding_model` must be updated.

- **`OpenAIEmbedder` moved to `retrievers.embedding`.** The old import path `evals.retrieval.embedder.OpenAIEmbedder` is gone; update to `from retrievers.embedding import OpenAIEmbedder`.

- **`chromadb` (full) replaced by `chromadb-client` across all packages.** `sentence-transformers`, `rank-bm25`, `langchain-experimental`, and `langchain-community` are no longer installed. Any code that called `SentenceTransformerEmbeddingFunction` or BM25/hybrid retrieval helpers must be rewritten.

- **`knowledge-retrievers` is now a base dependency of `knowledge-orchestrators`** (no longer opt-in via `[workbench]`). The `[workbench]` extras group is removed entirely; there is no longer a separate extras gate for RAG dependencies.

- **Production Docker image now includes `retrievers` source.** Previously excluded to avoid ML dep weight; with `chromadb-client` replacing the full `chromadb`+`sentence-transformers` stack, the separation is no longer needed.

### Removed

- **`[workbench]` extras, workbench code location, and all `idx_*` indexing strategies removed.** The `orchestrators.defs.workbench` code location is gone. `poe index`, `poe eval`, and `poe reset-indices` tasks are removed. `dagster-dev` no longer loads the workbench code location. `strategies.yaml`, `op_factories.py`, `StrategyPathsResource`, and all `idx_markdown_*` / `idx_recursive_*` / `idx_semantic_*` Dagster assets are deleted.

- **`eval-retrieval` CLI no longer requires `--extra workbench`.** The eval harness now runs from the base install; the `[workbench]` flag that was previously required to activate the dependency chain is gone.

---

## [0.9.7] — 2026-05-11

### Changed

- **Project framing in `CLAUDE.md` reoriented around the personal-knowledge-OS concept with two compounding exit ramps** — apply (research panel + codebase, owned by `newsletter-assistant`) and retain (wiki + indexing + retrieval, owned by this repo). Adds a "Where we are on the journey" section honest about what works today (backups + wiki synthesis) vs. WIP (index pipeline + evals) vs. aspirational (cross-corpus retrieval ranking, wiki-to-voice-agent bridge), plus a 4-point decision rubric for evaluating future work. Same framing applied in companion repo `newsletter-assistant`.

---

## [0.9.6] — 2026-05-11

### Changed

- **`domains.*` import paths reorganised for source-module symmetry.** `domains.store` is removed; `domains.wiki.sources.{RawStoreSource, LocalFileSource, IngestItem, IngestSource}` are split into their canonical homes. New paths: `RawStoreSource` → `domains.raw_store.sources`; `LocalFileSource` → `domains.notes.sources`; `IngestItem` and `IngestSource` → `domains.types`. Any external code (notebooks, downstream scripts) importing from the old paths will get `ImportError` and must be updated.

---

## [0.9.5] — 2026-05-11

### Added

- **Pinned retrieval eval dataset** (`packages/evals/datasets/retrieval_eval.jsonl`) — 166 labelled query/expected-`content_id` pairs spanning raw_store, sessions, notes, and research corpora. The `eval-retrieval` CLI now defaults `--eval-set` to this file, so `uv run eval-retrieval` works out of the box without specifying a path.

- **`LocalFileSource.get_item_ids` / `get_item`** — `LocalFileSource` in `domains/wiki/sources.py` now implements the full `IngestSource` interface (matching `SessionsSource`, `ResearchSource`, and the raw-store source). All four sources are now interchangeable in eval and ingestion loops.

### Changed

- **Embedding and Chroma upsert no longer hit provider limits on large corpora.** `OpenAIEmbedder` now sub-batches at a 250k-token budget per request (was: unbounded → OpenAI 400 at >300k tokens). Chroma upserts chunk at 4000 items (was: unbounded → server `max_batch_size` error above 5461). Both limits are internal; the CLI and runner API are unchanged.

## [0.9.4] — 2026-05-10

### Added

- **`evals.retrieval` harness + `eval-retrieval` CLI** — scores `(model, dims, chunker)` configs on Recall@5 / MRR@10 / nDCG@10 over expected `content_id`. `OpenAIEmbedder` with tenacity retry on transient errors only; on-disk embedding cache keyed on `(model, dims, sha256(text))`. Results in `data/eval_results/retrieval_<timestamp>.json`.

- **`SessionsSource` and `ResearchSource`** in `domains/{sessions,research}/` — read newsletter-assistant's `sessions.db` and `research.db`. Both assert WAL; `SessionsSource` gates on `ended_at IS NOT NULL`; `ResearchSource` reads `documents.content` directly (the writer commits it with the row).

- **`turn_grouping_chunker`** in `retrievers/chunking/turn_grouping.py`, registered as `"turn_grouping"` — packs consecutive transcript turns into `max_tokens`-bounded windows with overlap, preserving turn boundaries.

- **`packages/domains/README.md`** — layer-purpose doc.

- **`notebooks/` scaffold** + `[notebooks]` workspace extra + `notebooks` poe task — four exploratory notebooks for Phase C work; opt-in JupyterLab via `uv run poe notebooks`. `notebooks/README.md` documents a per-developer (gitignored) `.mcp.json` template for the Claude Code Jupyter MCP server.

### Changed

- **`IngestItem` and `Chunk` hoisted into a shared types module each** — `domains/types.py` for `IngestItem` (with optional `author`/`url`/`started_at`); `retrievers/chunking/types.py` for `Chunk`. Wiki and chunking modules re-export for back-compat.

- **`rag-eval` poe task renamed to `generation-eval`** (named for what it measures, not the library). New `retrieval-eval` task wired to the new harness; `wiki-eval` marked as TODO until that layer lands.

---

## [0.9.3] — 2026-05-09

### Changed

- **`BACKUP_DIR` and `BACKUP_SOURCE_DIR` are now required `dg.EnvVar`** (was: silent fallback to `<repo>/backups` and `~/newsletter-assistant/data`). Misconfigured deploys fail fast at run init. Symmetric with `DATABASE_URL`.

---

## [0.9.2] — 2026-05-09

### Changed

- **`wiki/synthesized` is now bound 1:1 to `backup_readings` snapshots by partition key.** Reads `BACKUP_DIR/<partition_key>/raw_store.db` directly; missing snapshot raises `dg.Failure` immediately — no filesystem scan, no freshness window, no fallback. `WikiResource.backup_dir` replaces `raw_store_db_path`. `SYNTHESIZE_WIKI_DAG_VERSION` 2 → 3.

- **Discovery extracted into a `wiki/pending` asset; schedule fires with no run config.** `wiki/pending` reads `raw_store ∖ wiki.processed` from the partition-bound snapshot, applies the per-tick cap, and emits the work order as `list[str]`; `wiki/synthesized` consumes it via `AssetIn`. `SynthesizeWikiConfig` removed — `Materialize all` from the UI now works without hand-typed run_config. Materialization metadata exposes `total_pending`, `queued`, `capped`, `excluded_by_source`, and `item_ids` for backlog visibility.

- **Source-prefix allowlist gates wiki discovery.** `ALLOWED_CONTENT_ID_PREFIXES = ("medium::",)` in `def_config.py` — only Medium articles enter the wiki today; other raw_store rows are skipped at discovery and counted in `excluded_by_source`. Widen the allowlist once per-source-type prompts land.

- **Per-tick cap is now `WIKI_MAX_PER_TICK` env var** (default 30). Was `MAX_PER_TICK_DEFAULT`, a code-level constant. Previously the executor was a `ThreadPoolExecutor`; synthesis now iterates sequentially — simpler, gentler on rate limits, and sufficient at cap 30 / daily cadence.

### Added

- **LLM cost metadata on every `wiki/synthesized` materialization.** Per-call usage flows through both wiki sub-graphs via a new `llm_calls` reducer; the asset attaches `cost_usd`, `input_tokens`, `output_tokens`, `cost_by_model` (JSON breakdown), and `cost_complete` (false when any item errored). New `workflows/costs.py` pricing table (gpt-4.1-nano, gpt-4.1-mini); unknown models surface in `unknown_pricing_models` rather than failing the run. New `generate_with_usage` / `generate_structured_with_usage` helpers in `workflows.llm`; the structured variant now re-raises `parsing_error` instead of silently returning `None`.

---

## [0.9.1] — 2026-05-08

### Changed

- **`backup_readings` snapshots `research.db` instead of archiving `research_output/`.** Newsletter-assistant's `research.db` already contains the research output, so the tgz was redundant. New asset `snapshots/research` mirrors `raw_store.db`/`sessions.db` (SQLite `.backup()` API + `PRAGMA integrity_check`). Existing `research_output.tgz` files in local backups + Drive are left in place; retention naturally prunes them. `BACKUP_READINGS_DAG_VERSION` 3 → 4.

---

## [0.9.0] — 2026-05-08

### Changed

- **`synthesize_wiki` is now schedule-driven, date-partitioned.** One scheduled tick (cron `0 6 * * *`) = one Dagster run = full pending → synthesized → index cycle. Discovery (`raw_store ∖ wiki.processed`) moved into the schedule; pending item_ids travel as `run_config` rather than per-item dynamic partitions. The asset fans out internally (ThreadPoolExecutor, cap 5) and re-filters against `wiki.processed` so retries don't re-pay for already-committed items.

- **`wiki/index` daily-partitioned with `deps=[wiki/synthesized]`** — the AllPartitionMapping trap is gone now that the partition dimension is bounded.

### Removed

- **`discover_pending_contents` asset and `wiki_items` dynamic partitions** — discovery is no longer an asset; it's a function the schedule calls at fire time. `WIKI_MAX_PER_DISCOVERY` env var dropped (replaced by code-level `MAX_PER_TICK_DEFAULT`).

---

## [0.8.0] — 2026-05-08

### Added

- **`synthesize_wiki` pipeline** — three assets (`discover_pending_contents`, `synthesize_item`, `regenerate_toc`) wrapping a LangGraph workflow that turns raw_store items into wiki pages. Per-partition retry auto-resumes from the LangGraph checkpoint.

- **`WIKI_MAX_PER_DISCOVERY` env var** — caps partitions registered per discovery (default 30). `LANGFUSE_TRACING_ENVIRONMENT` documented in `.env.example` for per-deploy trace tagging.

- **Wiki schema auto-applied on first `compose up`** via `docker/postgres/init/02-apply-wiki-schema.sh`. No manual `psql` step on fresh deploys.

- **Postgres exposed on `127.0.0.1:5432`** — laptop `dagster dev` reaches compose Pg without a port-forward. Loopback-only bind keeps Pg off the public NIC.

- **Upstream source `AssetSpec`s** (`raw_store`, `sessions`, `notes`) in `upstream_sources.py` — anchor lineage in the Dagster UI, connecting `backup_readings` and `synthesize_wiki` via shared upstream nodes.

### Changed

- **`discover_pending_contents` hard-fails above 10 000 raw_store items.** Full-scan discovery isn't right at that scale → migrate to sensor-driven discovery.

- **Partition keys source-prefixed** (`<source>:<id>`, e.g. `raw_store:abc123`) — avoids ID collisions across sources (notes, sessions, raw_store).

- **`DATABASE_URL` required** — `WikiResource` uses `dg.EnvVar`; unset → fails at run init. `get_checkpointer(db_url: str)` no longer accepts `None` or env fallback.

- **Discovery queries IDs only** via new `get_content_ids()` in `domains/store.py` — full content markdown no longer loaded for the diff.

---

## [0.7.0] — 2026-05-07

### Added

- **`notes/` and `research_output/` are now included in daily backups.** The `backup_readings` pipeline snapshots both directories as gzip-tar archives (`notes.tgz`, `research_output.tgz`), verifies each with a blocking integrity check (readable archive, at least one member), and includes them in the Drive upload and prune cycle. Missing source dir → hard `Failure`. Implementation: `snapshot_notes` / `snapshot_research_output` assets + `verify_snapshot_notes` / `verify_snapshot_research_output` checks in `backup_readings/`; `ARCHIVE_DIRS` in `config.py`; `BackupResource.expected_files` property. `BACKUP_READINGS_DAG_VERSION` 2 → 3.

---

## [0.6.7] — 2026-05-07

### Added

- **Co-emitted blocking checks on `storage_capacity` and `uploaded_snapshots`.** `drive_capacity_below_threshold` enforces the >90% Drive cap; the asset now always materializes so `used_pct` timeseries survives threshold violations. `all_snapshots_uploaded` re-lists Drive post-copy and catches rclone silent drops.
- **`DRIVE_BACKUP_ROOT` env var** (required) — path prefix under `DRIVE_REMOTE`. Lets dev deploys point at a sibling dir (`...-dev`) without overwriting prod. Unset → fails fast at run init.
- **`packages/orchestrators/STYLEGUIDE.md`** — checked-in conventions for Dagster pipelines in this repo.

### Changed

- **`check_drive_capacity` renamed to `storage_capacity`** (measurement-only; threshold moved to its blocking check). `BACKUP_READINGS_DAG_VERSION` 1 → 2.
- **`compute_kind` uses icon-supported names**: `rclone`/`google_drive` → `googledrive`; `filesystem` → `file`.
- **`backup_readings/README.md` reframed as a runbook** — failure-cascade diagram + ops + external setup; dropped code-mirroring tables.
- **Redundant `status: "ok"` metadata removed** from 5 assets.
- **`ping_healthcheck_on_success` sensor evaluated hourly** (`SENSOR_MIN_INTERVAL_S` 300 → 3600). Daily job — 60 min eval cadence is invisible against healthchecks' day+hour period+grace window.

## [0.6.6] — 2026-05-07

### Changed

- **Webserver binds two ports now**: `127.0.0.1:3030` always (SSH tunnel) plus `${APP_HOST:-127.0.0.2}:3030` for sibling-container reach. Default is a no-op loopback alias so `docker compose up` works without env. Previously, setting `APP_HOST=172.17.0.1` for Caddy silently broke the tunnel.

## [0.6.5] — 2026-05-07

### Changed

- **`DRIVE_REMOTE` and `HEALTHCHECK_PING_URL` are now required for `backup_readings`.** Previously unset → silent skip of Drive upload + healthcheck ping. Now → run fails fast at startup. Implementation: `RcloneResource` / `HealthcheckResource` use `dg.EnvVar()`; `is_configured` short-circuits removed.
- **Dagster webserver mounts at `/dags`** for reverse-proxy use. Docker UI is now at `http://localhost:3030/dags`; `poe dev` (local) is unchanged at `:3030`. Implementation: `--path-prefix=/dags` on the webserver entrypoint in `docker-compose.yml`; healthcheck probes `/dags/server_info`.
- **Deploy a feature branch from CLI** via `./scripts/deploy-hcloud.sh deploy --branch <name>` (default still `main`). Forwards through poe with the `--` separator: `uv run poe deploy -- --branch fix/foo --no-build`. Warn banner when the branch isn't `main`.
- **`APP_HOST` documented in `.env.example`** for operators fronting Dagster with a reverse proxy. Default `127.0.0.1` (laptop); set `172.17.0.1` on a server where a sibling Caddy container reaches Dagster via the docker0 gateway.

## [0.6.4] — 2026-05-07

Fix failed snapshot tasks in DAG "backup_readings" not raising error. Update `.env.example` to use simpler format.

### Added

## [0.6.3] — 2026-05-07

### Added

- **`APP_UID` / `APP_GID` in `.env`** — set them to the host deploy user's `id -u`/`id -g` so the container's `dagster` user shares the bind-mount owner's uid. No more chown ceremony for `./data`, `./logs`, `./backups`, `./.rclone`. Default 1001 keeps backward compatibility.
- **Per-step compute logs at `./logs`.** `LocalComputeLogManager` now writes to a bind-mounted `/app/logs` instead of the image default `/opt/dagster/storage`, which the non-root container user couldn't write.
- **Healthchecks for `dagster-webserver` (`/server_info` via stdlib urllib) and `dagster-daemon` (`liveness-check`).** Compose's `depends_on: service_healthy` now meaningfully waits on all three Dagster services.

### Changed

- **Production Docker image: ~17 GB → ~3 GB.** `knowledge-retrievers` was an unconditional dep of `workflows` even though only `workflows.agents` uses it; this transitively pulled in PyTorch + CUDA + chromadb. Moved to a `workflows[agents]` extra (activated via `orchestrators[workbench]`), so the prod image now installs only what wiki + backup pipelines need.
- **Container runs non-root with a real `$HOME=/home/dagster`.** Deliberate uid/gid (default 1001), no skel pollution. `langchain` added as an explicit `workflows` dep — `langfuse.langchain.CallbackHandler` needs the umbrella package and was previously pulled in only via workbench tooling.
- **`BACKUP_SOURCE_DIR` is now a host-path env consumed by compose.** Compose bind-mounts it to `/app/source` and overrides the in-container env var to that fixed path. Set in `.env` using `${HOME}/...` — compose doesn't tilde-expand.
- **Reproducible rebuilds.** Dagster framework image pins `dagster*` via build ARGs to match `uv.lock`; rclone pinned to v1.74.0 via precompiled binary (replaces `curl | bash`); both `uv sync` layers use BuildKit cache mounts so a one-package bump no longer re-downloads every wheel.
- **Faster startup.** `dagster-code` invokes `/app/.venv/bin/dagster` directly; the previous `uv run` form was re-syncing the workspace on every container start, pulling 5 workbench packages in ~13 s.
- **rclone config moves to `/home/dagster/.config/rclone` (rclone's default `$HOME` lookup).** No `RCLONE_CONFIG` env override needed.
- **Postgres image bumped 14 → 16.** Existing deploys: `pg_dumpall` against PG14 → recreate the volume → restore on PG16 before rebuilding.
- **`scripts/deploy-hcloud.sh` reads `DEPLOY_PASSWORD` from `.env.deploy`** and pipes it into remote `sudo -S`, so non-TTY SSH sudo works without a NOPASSWD sudoers entry.

### Removed

- **`RCLONE_CONFIG` env override** — redundant now that the config is mounted at the standard `$HOME/.config/rclone/` path.

---

## [0.6.2] — 2026-05-06

### Changed

- **Production Docker image drastically smaller** — `knowledge-retrievers` and `knowledge-evals` are now optional extras (`[workbench]`) in `knowledge-orchestrators`, so the production image no longer installs sentence-transformers, PyTorch, chromadb, or ragas (~8 GB of ML deps only needed for the RAG workbench). The image CMD now loads `orchestrators.defs.pipelines.definitions` (backup + wiki) instead of the full merger. Local dev is unchanged: `uv sync` at the workspace root still installs everything.
- **uv cache permission crash on deploy fixed** — the `dagster` container user is created without a home directory, causing uv to fail creating `~/.cache/uv` at startup. `ENV UV_NO_CACHE=1` is now set in the Dockerfile so no home directory is needed.

---

## [0.6.1] — 2026-05-06

### Changed

- **`dagster-code` now loads `.env` via `env_file`** — eliminates manual `DAGSTER_POSTGRES_*` duplication in docker-compose. `dagster-webserver` and `dagster-daemon` retain only their built-in env vars (least-privilege). Implementation: `docker-compose.yml` `dagster-code` service.
- **Dev tunnel port corrected to 3030** — was 3000, which mismatched the `poe dev` / `poe tunnel` host port. `.env.example` reorganised by consumer group for clarity.

---

## [0.6.0] — 2026-05-06

Revamp DAG "backup_databases" to "backup_readings". The tasks are configured following best practices from "dagster-open-plaform" project. DAG will backup sqlite databases in local and on cloud at Google Drive.


## [0.5.0] — 2026-05-06

Phase B — LangGraph wiki synthesis migration. The wiki synthesis pipeline is now a checkpointed LangGraph workflow with Send-API per-entity fan-out, transactional Postgres commits, and dynamic-partitioned Dagster materialization. Manual real-data validation deferred to a follow-up PR.

- **`workflows/shared/`** — new home for cross-workflow plumbing. `checkpointer.get_checkpointer(db_url=None)` is a context manager that yields a `langgraph.checkpoint.postgres.PostgresSaver` bound to a fresh psycopg connection; falls back to `DATABASE_URL` env var; calls `setup()` on entry. `observability.get_langfuse_callback()` returns a process-cached `langfuse.langchain.CallbackHandler` when `LANGFUSE_PUBLIC_KEY` is set, otherwise `None` (no warning).
- **`workflows/llm.py`** — both `generate` and `generate_structured` now pass `config={"callbacks": [...]}` to LangChain when the Langfuse callback is configured. No-op when env unset; existing behavior unchanged.
- **`domains/wiki/state.py`** — Postgres helpers backing the new workflow's terminal commit. Pure functions taking a `psycopg.Connection`; callers manage transactions. `insert_processed`, `get_processed_ids`, `get_failed`, `upsert_page`, `get_page`, `get_all_pages`, `insert_aliases_idempotent` (uses `ON CONFLICT (alias) DO NOTHING` for concurrent-partition safety), `snapshot_aliases` (reads aliases into the existing in-memory `AliasStore`).
- **`pytest-postgresql>=6.0,<8.0`** added to root dev deps. `tests/conftest.py` exposes a `wiki_pg` fixture that yields a fresh psycopg connection to a temp Postgres with `wiki.sql` loaded — used by `tests/wiki/test_state_pg.py` (11 new tests covering all helpers plus PK/upsert/concurrency edges).
- **`workflows/wiki_synthesis/`** — the new LangGraph workflow that replaces `workflows/wiki/ingest.py` (the legacy folder is now removed; see "PR 3 — rewire and cleanup" below). Files:
  - `graph.py` — parent `StateGraph` (one document per invocation). `extract_entities` → conditional fan-out via `langgraph.types.Send` → per-entity sub-graph → `commit`. The `WikiSynthesisState` TypedDict declares an `Annotated[list[dict], operator.add]` reducer on `entity_results` so concurrent sub-graphs concatenate their results into the parent state without collision.
  - `entity_graph.py` — per-entity sub-graph with one node (`process_entity`) and a restricted `EntityWorkflowOutput` schema so only `entity_results` flows back to the parent (avoids the `InvalidUpdateError` that LangGraph 1.x raises when multiple Sends try to write the parent's input keys).
  - `nodes.py` — `extract_entities` snapshots aliases from Postgres, calls the extraction LLM, stages new aliases for commit. `commit` opens one Postgres transaction and writes `wiki.pages` rows for each successful entity, `wiki.aliases` for staged tuples (`ON CONFLICT DO NOTHING`), and the single `wiki.processed` row — atomic per the plan's "same transaction" rule. Status mapping covers ok / error / skipped / partial-success.
  - `parsing.py` — pure helpers (`parse_llm_page_output`, `check_h2_preservation`, `slug_from_id`) lifted out of legacy `ingest.py` so the new workflow doesn't depend on the soon-to-be-deleted module.
- **`workflows/wiki_synthesis/` invocation pattern** — caller compiles the graph with a checkpointer from `workflows.shared.checkpointer.get_checkpointer()` and invokes with `thread_id=f"wiki_synthesis__{item_id}"`. Replay after crash resumes from per-entity sub-graph checkpoints; only failed entities re-run the synthesis LLM call.
- **`wiki.processed.status` CHECK constraint** added to `wiki.sql` — values must be `'ok'`, `'error'`, or `'skipped'`. Prevents silent drift if a future caller writes a different string.
- **Extraction failures now write a status='error' processed row** (new behavior — legacy raised and left no DB footprint, causing infinite Dagster retries on a permanent extraction failure).
- **Parity tests** ported from `tests/wiki/test_ingest.py` to `tests/wiki_synthesis/{test_parsing,test_stage_aliases,test_graph}.py`. 16 new tests; LLM calls mocked at import locations, Postgres assertions run against the real `wiki_pg` fixture so the transactional commit path is genuinely exercised.
- **`workflows/wiki_synthesis/runner.py::invoke_wiki_synthesis`** — the canonical invocation pattern. Bundles PostgresSaver checkpointer, `thread_id="wiki_synthesis__{item_id}"`, the Langfuse callback at `graph.invoke` level (not just per-LLM-call), and `langfuse_session_id` + tags metadata so retries/replays of the same item group into one Langfuse session view. Callers (Dagster asset in PR 3, future CLI, ad-hoc scripts) should prefer this over compiling the graph manually. Three new tests cover end-to-end invocation, callback propagation when `LANGFUSE_PUBLIC_KEY` is set, and silent omission when unset.
- **PR 2 new-capability test suite** under `tests/wiki_synthesis/`. Verifies the properties that justify using LangGraph at all:
  - **Replay correctness** (`test_replay.py`) — when `commit` raises mid-txn, the post-fan-out checkpoint is preserved; resuming via `graph.invoke(None, config)` re-runs only `commit`. The synthesis LLM is **not** re-called (the architecturally important property — no LLM cost on commit-time DB failure). Note: the original "per-entity replay" claim was overstated; Send fan-out is one atomic super-step from the checkpointer's perspective. Documented in the test docstring.
  - **Send-API parallelism** (`test_parallelism.py`) — N synthesis calls routed through `threading.Barrier(N, timeout)`; barrier only releases when all N threads arrive. If Send were serialized, only one thread would arrive and the test fails fast.
  - **Commit txn atomicity** (`test_commit_atomicity.py`) — patching `insert_processed` or `upsert_page` to raise mid-txn aborts the whole transaction; `wiki.pages`, `wiki.aliases`, `wiki.processed` all stay empty. The `.md` files DO exist on disk (`write_page` is file-atomic, outside the txn boundary) — pinned in the test as intentional.
  - **Concurrent alias safety** (`test_alias_concurrency.py`) — 10 threads in two groups of 5 race for the same two aliases via separate psycopg connections; `ON CONFLICT (alias) DO NOTHING` lands exactly one row per alias, no deadlock, no exception.
- **`runner.py` auto-resume** — `invoke_wiki_synthesis` now checks `graph.get_state(config).next`. If the thread is paused mid-execution (prior invocation raised before END), it invokes with `None` to resume. Otherwise (non-existent thread or successfully ended thread) it invokes with the input state for a fresh run. Critical because `invoke(state, config)` on an existing thread restarts from START — silently re-firing every entity LLM call. The completed-thread re-run case is intentional (Dagster re-materializing should re-run with current inputs) and pinned by `test_runner_re_runs_on_completed_thread`.
- **`pytest-timeout>=2.3,<3.0`** added to dev deps so threading-based tests use `@pytest.mark.timeout(N)` and never hang the suite.
- **Shared test helpers** at `tests/wiki_synthesis/_helpers.py` (`make_item`, `make_extraction`, `build_synthesis_output`, `extract_entity_id_from_prompt`) — extracted from the parity tests so the new-capability tests don't duplicate factories.

#### PR 3 — rewire and cleanup

- **`wiki_synthesized` Dagster asset** is now dynamic-partitioned (`DynamicPartitionsDefinition(name="wiki_items")`) and invokes `invoke_wiki_synthesis(item, db_url, wiki_dir)` per partition. Per-partition retries auto-resume from the LangGraph checkpoint via the runner's auto-resume heuristic — no LLM re-calls if the prior failure was in commit.
- **`wiki_pending`** becomes a discovery asset: it scans raw_store for items not in `wiki.processed` (status=ok or skipped) and registers them as new `wiki_items` partitions on the Dagster instance. Idempotent re-runs only add what isn't already a partition.
- **`wiki_index_updated`** reads from `wiki.pages` (Postgres) via `domains.wiki.state.get_all_pages`. Intentionally NOT declared as `deps=[wiki_synthesized]` because the default `AllPartitionMapping` would block the index on every partition completing — never true with dynamic partitions. Phase E will add a sensor; for now, materialize manually.
- **`WikiResource`** gains a `database_url` field that resolves at call time via `get_database_url()` (Dagster idiom — pass `dg.EnvVar("DATABASE_URL")` at construction or rely on env-var fallback). Removed `state_db_path` and `aliases_path` (legacy SQLite/YAML).
- **`domains.store.get_content_by_id(content_id, *, db_path)`** — focused single-row lookup the partitioned asset needs (vs. loading every row via `get_contents`).
- **`domains.wiki.sources.RawStoreSource.get_item(item_id)`** — convenience wrapper on top of `get_content_by_id`.
- **Deleted**:
  - `packages/workflows/src/workflows/wiki/{__init__,ingest,state}.py` and the now-empty `wiki/` folder.
  - `tests/wiki/test_ingest.py`, `tests/wiki/test_state.py` — the parity tests for the deleted code, now redundant with `tests/wiki_synthesis/`.
- **Moved**: `workflows/wiki/prompts.py` → `workflows/wiki_synthesis/prompts.py` (still in use by `entity_graph.py` and `nodes.py`; imports rewired).

---

## [0.4.0] — 2026-05-01

Phase A foundation release — restructure into a uv workspace with Postgres infrastructure ready for the LangGraph wiki workflow (Phase B). No behavior change; 90/90 tests pass.

- **uv workspace with 5 packages** under `packages/{domains,workflows,retrievers,evals,orchestrators}/`. Cross-package import discipline enforced via `pyproject.toml` deps:
  - `knowledge-domains` — pure data layer (pydantic, psycopg, pyyaml, python-frontmatter); no LLM/ML/Dagster deps.
  - `knowledge-workflows` — depends on domains + retrievers; adds langgraph, langgraph-checkpoint-postgres, langchain-openai, langchain-anthropic, langfuse.
  - `knowledge-retrievers` — depends on domains; adds chromadb, sentence-transformers, rank-bm25, langchain-text-splitters, langchain-experimental, langchain-community, tiktoken.
  - `knowledge-evals` — depends on domains + workflows + retrievers; adds ragas.
  - `knowledge-orchestrators` — depends on all four others; the only package allowed to depend on Dagster, dagster-postgres, dagster-webserver, dagster-dg-cli, poethepoet.
- **Code migrated into the 5 packages** — the old `src/knowledge_pipeline/` tree split across the new packages and deleted:
  - `domains/`: `store.py`, `wiki/{types,io,aliases,sources}.py`, `wiki/schema/wiki.sql`
  - `retrievers/`: `chunking/`, `postprocess/`, `retrieval/` (cosine, hybrid, rerank, fusion, registry), `vector_store/chroma.py`
  - `workflows/`: `llm.py`, `wiki/{prompts,state,ingest}.py`, `agents/nodes/query_rewrite.py` (was `lib/retrieval/hyde.py`)
  - `evals/`: `rag.py` (was `lib/eval.py`)
  - `orchestrators/`: `definitions.py`, `config.py`, `strategies.{py,yaml}` (the `.py` was `lib/utils.py`), and the entire `defs/` tree (`shared/`, `workbench/`, `pipelines/`).
- **Boundary fixes** — `domains.store` and `retrievers.vector_store.chroma` no longer import `orchestrators.config`. Path arguments (`db_path`, `chroma_path`) are now passed in by the caller. `db_path` is keyword-only across all `domains.store` functions. `HyDE` retrieval has an LLM dep so it lives in `workflows.agents.nodes.query_rewrite`, not in `retrievers`.
- **Workspace package pip-names prefixed with `knowledge-`** — matches the `newsletter-assistant` workspace pattern: prefix lives only in `pyproject.toml` `dependencies` and `[tool.uv.sources]` keys; import paths stay plain (`from domains import …`). Pre-empts cross-project name collisions when `newsletter-assistant` consumes `knowledge-workflows`.
- **Root project shape** — `pyproject.toml` is now a virtual project: `[project].dependencies` lists all 5 prefixed members, no `[build-system]`, no `[tool.hatch.*]`. Bare `uv sync` (without `--all-packages`) installs the full workspace.
- **Postgres `knowledge_pipeline` database** — idempotent `docker/postgres/init/01-create-knowledge-db.sh` mounted into the existing Dagster postgres service via `/docker-entrypoint-initdb.d` (single instance, no second container).
- **`packages/domains/src/domains/wiki/schema/wiki.sql`** — Postgres schema for wiki state: `wiki.processed` (PK `(item_id, source_type)`), `wiki.pages` (PK `entity_id`, jsonb columns for `related`/`sources`/`source_types`), `wiki.aliases` (UNIQUE `alias`, indexed by `entity_id`). Ready for Phase B.
- **Dagster code locations preserved as workbench / pipelines** — `poe dev` loads both `orchestrators.defs.workbench.definitions` and `orchestrators.defs.pipelines.definitions` as separate code locations (matches the original split). `orchestrators.definitions` is the merger used by Docker/prod (single `-m` flag in `configs/workspace.yaml` + `docker/code/Dockerfile`). `[tool.dagster].module_name` points at the merger; `code_location_name` is `orchestrators`.
- **CLI migrated from `dagster job execute` to `dg launch`** — Dagster 1.13 deprecated the former. `poe index/backup/eval` now use `dg launch -m … --job …`. Added `dagster-dg-cli` to the orchestrators deps.
- **Dev port moved from 3000 to 3030** — `poe dev`, `poe tunnel` (host side), `docker-compose` host port mapping, README references. Container internal port and remote production port stay at 3000.
- **Docker code-server image** rebuilt for the workspace layout — copy each member's `pyproject.toml` first for layer-cached deps resolution, then per-member `src/` for editable installs. Switched from `pip install uv` to a binary copy from `ghcr.io/astral-sh/uv:latest` (~150s faster). Use `--no-install-workspace --package knowledge-orchestrators` so only the deployable's dep tree is resolved.
- **New poe tasks** (stubs for Phase B) — `wiki-ingest`, `wiki-lint`, `research`, `wiki-eval`, `rag-eval`, `reset-wiki`, `reset-checkpoints`, `reset-everything`.
- **Removed** `src/knowledge_pipeline/` — the entire old source tree.

## [0.3.0] — 2026-04-28

### Added

- **Wiki synthesis pipeline** — LLM-powered knowledge distillation that reads raw articles and produces wiki pages
  - Entity extraction (gpt-4.1-nano) identifies concepts, tools, and trends
  - Page synthesis (gpt-4.1-mini) creates or updates wiki pages per entity
  - Asset-based Dagster architecture (`wiki_synthesized`, `wiki_pending`, `wiki_index_updated`)
- **`lib/wiki/`** — core library with no Dagster dependencies:
  - `types.py` — Pydantic models (WikiPage, ExtractedEntity, ExtractionResult)
  - `io.py` — markdown + YAML frontmatter read/write with atomic writes
  - `aliases.py` — entity alias resolution with fuzzy matching (difflib, 0.85 threshold)
  - `state.py` — SQLite state tracking (WAL mode, transactional updates)
  - `sources.py` — source adapters (RawStoreSource, LocalFileSource)
  - `ingest.py` — orchestration: extract → synthesize → write → update state
  - `prompts.py` — LLM system/user prompts
- **Robustness** — atomic file writes (`os.replace`), transactional state DB, LLM output validation, staged alias persistence

---

## [0.2.0] — 2026-04-20

### Added

- **LLM client** — `lib/llm.py` with LangChain wrapper (`generate`, `generate_structured`) for provider-agnostic LLM calls with Pydantic-validated structured output
- **Wiki config** — `wiki` section in `strategies.yaml` (synthesis model, page types, collection name, embedding model)
- **`langchain-openai`** dependency (replaces direct `openai` SDK)
- **CHANGELOG.md** — initial changelog covering project history

---

## [0.1.0] — 2026-04-20

Initial baseline. Dagster-based RAG strategy workbench with evaluation harness.

### Added

- **Index strategies** — four pluggable chunking + embedding combinations:
  - `idx_markdown_minilm` — markdown-aware chunking + MiniLM (baseline)
  - `idx_markdown_bge` — markdown chunking + BGE-small-en-v1.5
  - `idx_recursive_minilm` — recursive character splitting + MiniLM
  - `idx_semantic_minilm` — semantic chunking (embedding similarity splits) + MiniLM
- **Retrieval strategies** — four retrieval methods:
  - `cosine` — basic vector similarity
  - `rerank` — two-stage with cross-encoder reranking
  - `hybrid` — BM25 + vector + Reciprocal Rank Fusion
  - `rerank_hybrid` — hybrid candidates reranked by cross-encoder
- **Evaluation harness** — ops-based job comparing all (collection x retrieval) combos with recall@k, precision@k, MRR metrics across 40 curated queries
- **Chunking registry** — pluggable chunking strategies via `lib/chunking/registry.py`
- **Op factories** — `create_chunk_batch_op`, `create_embed_batch_op`, `create_index_op` for strategy-specific Dagster ops
- **Static dataset** — pinned `raw_store.db` snapshot for reproducible evaluation
- **Database backup job** — scheduled backup of SQLite and ChromaDB data
- **Docker deployment** — Dockerfiles and docker-compose with separate code location server
- **SSH tunnel task** — `uv run poe tunnel dagster` for remote UI access
- **Code locations** — split into `workbench/` (index + eval) and `pipelines/` (backup)
