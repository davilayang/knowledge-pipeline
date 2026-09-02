# narrative_v2 — anchored threads-first extraction (call 1 of 3)

**Superseded by `narrative_v3.md`, and no longer runnable.** This prompt's field list was
generated from `domains.extraction.schemas.Narrative`, and that model now carries v3's six
sections — so sending this body would ask the model for `salient_threads` while handing it a
schema without one. Kept as the record of what the threads-first shape said and why.

Produces the *narrative* output the live voice agent reads for "tell me about
this content." Topic Card and follow-ups are separate calls
(`topic_card_v1.md`, `followups_v1.md`).

## What changed from narrative_v1 (and why)

`narrative_v1` filled a fixed ~8-section basket (Core idea / What's new /
Named concepts / Mechanism / Takeaways / Key claims / Quotes / Examples),
optimizing *section-completion* over *thread coverage*. A 7-source /
137-thread gold eval showed it systematically drops story arcs, argument
structure, operational war-stories, and relational threads — anything the
fixed sections had no slot for — regardless of length.

`narrative_v2` is **threads-first**: enumerate every distinct follow-up thread
in the source, cover the whole source (not just the opening), catalogue every
item when the source is a list, and carry every figure/benchmark/named study
as a mandatory anchor. On the gold set this lifted coverage 71%→85% at
gpt-4.1-mini with no routing or schema change, and never regressed a shape.

## What changed within narrative_v2: json instead of markdown

The section rules above are unchanged — same three sections, same wording. Only
the output container moved, and the reason is the prompt cache.

This call used to send `[system: this prompt, user: article]` with no
`response_format`. That put it in a prompt-cache partition of its own, for two
independent reasons: it led with a different system message than the other two
calls, and OpenAI partitions the prefix cache by `response_format`. The article
was therefore a cache miss on this call and a cache write on the topic card
call — billed twice per item.

Returning json lets this call go through `shared_prefix.structured_messages`
with the same `{"type": "json_object"}` its siblings send, so all three share one
`[SHARED_SYSTEM, article]` prefix. The narrative runs first and writes it; the
other two read it. The article is billed once. Measured on a 5,028-token
article: the topic-card and follow-ups calls each read 4,864 cached tokens,
where the topic card previously read none.

`prompt_label` deliberately stays `narrative_v2` across that change. The two
shapes are already told apart by `extraction_calls.schema_name` — NULL on the
markdown rows, `Narrative` on the json ones — which is also what the consumer
branches on, so the label does not need to carry the distinction as well.

Two consequences for whoever edits this file:

- **The prompt no longer leads the request** — it rides in the task tail, after
  the article. Its length has no effect on the cache, so there is no minimum
  size to preserve.
- **The section headers are no longer written by this prompt.** They come from
  the `title` on each field of `domains.extraction.schemas.Narrative`, and
  `domains.extraction.render.render_narrative` emits them. Renaming a section
  means editing the model, not this file.

## What is NOT in this file

The output-format instruction and the field list are generated from the
`Narrative` pydantic model by `shared_prefix.schema_block()` and appended to this
text at call time. Do not restate them here — two sources for the same contract
is how they drift apart.

The prompt-injection guard is also absent by design: `SHARED_SYSTEM` now leads
the request and carries it, ahead of the untrusted article. This file had its
own copy while it had no shared system message.

Everything below the horizontal rule is the prompt body (model-facing).
Everything above it is design notes and is stripped at load
(`domains.extraction.strip_design_notes`) — it never reaches the model.

---

You extract structured information from articles, podcasts, YouTube transcripts, and newsletter digests for a voice AI agent that helps the user *learn new ideas* and *recall past learnings*. The agent reads your output aloud when the user says "tell me about this content", so your job is to capture EVERYTHING a listener might want to ask a follow-up question about — not to write a tidy summary.

CONTENT-TYPE ROUTING
- The caller prepends a [content_type: ...] tag to the user message. Use it to route — do NOT emit it as an output field.
- Articles from Medium/web often arrive with site chrome (Sign in / Open in app / Sitemap). Skip the chrome; extract from the body only.
- Podcasts and YouTube interviews have multiple speakers — attribute by speaker name when detectable.

`salient_threads` — THE PRIMARY OUTPUT
Enumerate the DISTINCT threads in the source — a thread is a claim, finding, argument, method, result, story beat, comparison, objection, statistic, or framing that a listener could ask a SEPARATE follow-up question about. Rules:
- Cover the WHOLE source, beginning to end. Do not front-load; a source's later sections (results, implications, war-stories, caveats, per-item details) carry as many threads as its opening.
- Scale the count to the source: a short single-point essay may have 5-8 threads; a dense research paper, long talk, or wide-ranging interview will have 15-30+. Do NOT stop at a fixed number — stop only when you run out of source-grounded threads.
- If the source is organized as a LIST, catalogue, or set of parallel items (patterns, steps, categories, research questions, benchmarks, named systems), enumerate EVERY item — do not sample a few and summarize the rest. One thread per item, each keeping that item's specific detail.
- FIGURES ARE MANDATORY, NOT OPTIONAL. If the source attaches a number, percentage, benchmark score, count, date, price, or measured quantity to a point, that exact figure MUST appear in the thread. Never replace a figure with a qualitative description ("significantly improved", "the majority", "a large dataset") — carry the number. A thread that had a number in the source but drops it is a failure.
- Preserve the source's own shape. If it argues, keep the argument and its concessions; if it compares, keep both sides; if it's a sequence of stages/steps, keep the order; if it's a case study, keep the specifics; if it tells a story, keep the beats.
- Every thread MUST carry at least one concrete anchor lifted from the source: a named entity, a figure (per the rule above), a mechanism, a specific example, or a short quoted phrase. A thread with no anchor is not a thread — drop it.
- Do NOT collapse multiple distinct threads into one generic sentence ("the paper reports several benchmark results" is a failure — name each benchmark and its score).
- Do NOT invent, bridge, or synthesize beyond the source. Do NOT pad to look thorough. If the source rehashes consensus with nothing new, say so in one thread and move on.
- One thread per DISTINCT point. Do NOT split a single point across multiple threads, and do NOT spin a sub-clause or a restatement into its own thread to inflate the count — over-production is as much a failure as collapse. A short, simple source has few threads, and a handful is the correct answer for it.
Format each thread as one string: a short label, then a dash, then the specific content with its anchor(s). Attribute to a speaker when the source names one. Plain text inside the string — no markdown syntax, no numbering of your own.

`core_idea`
1-2 sentences. The single thing worth knowing if you remember nothing else.

`named_concepts_and_entities`
One comma-separated string. Named individuals (creator / host / guest / author) first, then companies, products, techniques. The named guest in an interview outranks all side-mentions — never drop a named human to fit a company name.
