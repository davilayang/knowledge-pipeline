# narrative_v3 — six sections, claims-first, with a delivery layer (call 1 of 3)

Produces the *narrative* the live voice agent reads for "tell me about this content". Topic Card
and follow-ups remain separate calls (`topic_card_v1.md`, `followups_v1.md`).

## This is the live narrative prompt

`PROMPT_LABEL_NARRATIVE` points here and the call validates against
`domains.extraction.schemas.Narrative`, which carries the six sections below.

**There is one `Narrative` class, not one per prompt version** — the same rule `TopicCard` and
`Followups` follow. `schema_block()` generates this prompt's field list from that model, so the
two describe each other and cannot be versioned apart: a new narrative prompt reshapes
`Narrative` in the same commit that repoints the label. `narrative_v2.md` is kept as history and
can no longer be run, because the shape it describes no longer exists.

## What changed from narrative_v2, and why

**v2's `Salient threads` is gone, not shrunk.** It was the primary output and it carried a
measured padding failure: a 598-character Facebook post produced 19 threads, four of them
statements *about* the post — `Tone and rhetorical device`, `Target audience and applicability`,
`Emotional/psychological underpinning`, `Link/source`. v2's own anti-padding rules were present
and were not holding.

`load_bearing_claims` replaces it as the inventory, and is better on every axis that matters
here. It is defined by a filter question rather than by exhaustion, so it runs 9-28 entries
(median 15) instead of up to 54. It is size-independent — across a 44x span of source length the
count grows 2.2x. And it is a "go on" queue that walks claims the piece collapses without,
rather than one that walks into `Tone and rhetorical device`.

**One thing v2 had was dropped by mistake and is now back: concessions.** v2 said "if it
argues, keep the argument and its concessions; if it compares, keep both sides". v3 deleted
that and replaced it with nothing — and the filter question actively works against it, since a
concession is by definition something the piece survives without. Measured on the coverage gold:
concessions are 17.6% of one argument fixture's threads but were 44.4% of the threads the model
missed (2.5x, one-sided p=0.031). The repair is the exception bullet inside
`load_bearing_claims`, not v2's sentence restored verbatim, which would contradict the filter
question rather than complete it. With the bullet, concession recall on that fixture went
0.500 to 0.833 with the claim count unchanged, and a listicle fixture was byte-identical across
both arms — the carve-out does not manufacture caveats where a source has none.

**Four sections are new**: speakers, structure, the claim inventory, and ordered beats. The
agent needs something to deliver *incrementally* — prose offers nothing to stop at, and it was
measured speaking ~943 characters after a fetch to a listener whose median reply is ~43.

**The section order changed.** See "Order is load-bearing" below.

## Order is load-bearing in two places

The model writes top to bottom, so field order is generation order.

1. `speakers_and_author` — a factual lookup. Frames nothing that follows, so it is free to go first.
2. `structure` — **commits the shape before any content is written.** This is the anti-flattening move.
3. `core_idea` — the only measured ordering constraint: it must not be first.
4. `load_bearing_claims` — must precede the beats, which compress it and cite it by position.
5. `delivery_beats` — hard generation dependency on 4.
6. `named_concepts_and_entities` — a roll-up over everything already written.

**The measured constraint, in full.** v1 put a mandatory `core_idea` first and it flattened
shape: a model shown only the narrative predicted `argues_one_thesis` on 37 of 40 items, and
within-item went from 65% to 95% `argues_one_thesis` reading the narrative rather than the raw
content, with distinct labels collapsing 4 to 2. Per-label recall was `builds_in_order` 0/10,
`has_no_throughline` 0/4, `declares_its_list` 1/7. **Do not promote `core_idea` back to first.**

Positions 1, 2 and 6 are reasoned from generation dependencies, not measured. No probe has
compared orders directly.

## `core_idea` is conditional now

Position was not the whole fix. A mandatory core idea presupposes there is one, so when the
piece is a bundle the model manufactures a thesis over it — the same flattening, arriving
through the field instead of through its position. It is now conditioned on the field above it:
name the idea for a throughline or a sequence, say what the set is *of* for independent threads.

It stays complete — it always answers. It just stops forcing a spine onto a bundle.

## The counts are rendered, not asked for

`load_bearing_claims` and `delivery_beats` are json arrays, so
`domains.extraction.render.render_narrative` counts them and writes the count into the header
the agent reads: `Load bearing claims (15):`, `Delivery beats (5):`. It numbers the entries too.

This is why **the prompt asks for no counts and no numbering on the two list sections**. An
earlier draft had the model self-report `(N)` and `(M of N)` in free text, which needed three
invariants in the extractor to catch the arithmetic slip that would otherwise reach the listener
as *"that's six of the fifteen"* over a list of twelve. Counting the array removes the arithmetic
rather than checking it.

**`structure` is the one place a number is still self-reported** — `N independent threads` counts
threads in the *source*, not entries in this output, so there is no array to derive it from and
nothing validates it. Treat it as a shape label that happens to carry a number, not as an
inventory size.

What the derived counts buy is narrower than it looks: the number cannot disagree with the list
it counts, which is what makes the remainder arithmetic safe. It cannot make the list itself
honest — a padded or duplicated inventory renders a truthful count of a bad set, which is why
`load_bearing_claims` carries its own rule against entries about the piece.

The subtraction stays on the consumer side: the agent has both numbers and computes the
remainder at delivery time, because only it knows how far into the beats a session has got.

## Why the section is `Load bearing claims`, unhyphenated

newsletter-assistant renders the stored json back to text on its own side, deriving each header
from the json key (`load_bearing_claims` gives `Load bearing claims`) rather than by mirroring
the model — which is what lets a new section ship from this repo alone. The derivation cannot
produce a hyphen. `tests/domains/extraction/test_render.py` pins every title to that derivation,
so the alternative was teaching both repos one explicit exception for one word. The hyphen is
not worth a cross-repo special case.

## Before merging: the token ceiling

`ExtractorRegistry.max_tokens` is 4096. Measured on gpt-5.6-luna, a 71k-character podcast used
**4,057 of 4,096** on v3's earlier seven-section shape, and a 41,000-character newsletter
returned `finish_reason: length`.

Cutting `Salient threads` should buy most of that back, but json escaping spends it again — and
under json mode a truncated reply is not a short narrative, it is invalid json that burns every
retry and fails the item. So it was measured rather than assumed, on `gpt-5-mini` against three
real sources at the live 4096 ceiling:

| source | characters | output tokens | headroom |
|---|---|---|---|
| Article | 70,740 | 1,930 | 2,166 |
| YouTube | 99,202 | 2,131 | 1,965 |
| YouTube | 232,852 | 1,330 | 2,766 |

The cut paid for the escaping with room to spare — the ~70k article is the size that spent 4,057
of 4,096 under the seven-section shape. The output no longer scales with source length, because
the claim inventory caps where threads did not: the 232k source produced the *smallest* output.

**Re-measure before adding a section**, and treat 4096 as binding rather than nominal.

## Why the language line exists, and where it does NOT apply

No prompt here ever stated an output language, so English was an emergent property of whichever
model was running — and it did not survive a model change. On a 50%-Chinese source `gpt-5-mini`
returned a narrative at 0.0% CJK and `gpt-5.6-luna` at 55.4%.

The rule is Latin script, not merely English, because this artefact is **spoken** through a
synthesiser that cannot pronounce Chinese — a preserved phrase is garbled audio, not a vivid
detail. It reaches names too: four corpus items carry CJK author names that land in
`speakers_and_author`.

**Do not copy this rule into a read artefact.** Wiki pages and notes are read on screen by
someone who speaks the source language; flattening the original phrasing there loses the hook
and buys nothing. The constraint belongs to the consumer, not to extraction.

## Deliberately NOT added

- **`Supporting detail:`** — that was the second tier, and cutting `Salient threads` cut it.
  `ask_about_content` re-reads the raw source and is the second tier now.
- **`Remainder:`** — the extractor cannot see session state. The remainder is computed at
  delivery time from the two rendered counts.
- **`Assumed background:`** — glossing on first use is a delivery-time judgement made from the
  listener's vocabulary, not decidable from the source.

## Scars — do not re-introduce

- **No instructions inside `structure`.** A probe put one there and it was ignored in every
  session at every reasoning effort. That field describes shape; anything the agent must *do* is
  a beat or a rule with a trigger.
- **No source text as a pointer.** A field quoting a source fragment made the agent call the
  lookup tool 0 of 5 times and reproduce exactly the sentence it was shown and neither
  neighbour. A label triggers a lookup; a fragment suppresses one.
- **4-6 beats is a measured budget, not a guess.** Sources carry 9-28 load-bearing claims
  (median 15), so the cap always binds and a beat never has to be invented. Changing it needs
  new measurement, not intuition.
- **Padding is a shape, not a length.** The failure that killed `Salient threads` was entries
  *about* the piece — tone, target audience, emotional underpinning, the source link. The same
  shape can infect `load_bearing_claims`, which is why it carries its own rule against it.
- **A beat compresses a structural unit; it is not a claim picked off the inventory.** Beats used
  to be a shortlist — six of fifteen claims, reworded — and nothing recorded which six, so the
  consumer held an inventory it could not connect to the beat it had just spoken. Two changes
  together: a beat now states the point of every claim in one unit of the source, and names those
  claims by position. Which units exist comes from `structure`, which is what stops the rule
  fabricating on list-shaped sources: with twelve independent threads and a six-beat cap, a flat
  "compress several claims per beat" rule has to merge unrelated threads, and merging what a
  source keeps apart is the same failure that removed `bridge_to` below. Under the structural
  rule the model covers six threads and leaves six, which is honest and countable.
- **No `bridge_to` line inside a beat.** Each beat used to name what the next one covered. It was
  removed on evidence from both ends: the consumer never spoke the label and its own phrasing was
  more specific, and across 134 bridges the phrase restated a median 43% of the following beat's
  content words — a partial duplicate of text already present in full. It was also the only
  snake_case token in an artefact read aloud, and it let a beat manufacture a causal link between
  items a listicle merely places side by side. The chain survives without it: beats still reuse a
  name, term or figure from the beat before, which is the rule that carries the handover.

## What is NOT in this file

The output-format instruction and the field list are generated from the `Narrative` pydantic
model by `shared_prefix.schema_block()` and appended to this text at call time. Do not restate
the **format** here — the key names, the json container, the required set.

The rules below and the model's `description=` text do overlap, deliberately: the description is
the compressed contract the schema block carries, this file is where the rule and its evidence
live. Both reach the model in one request, so where they overlap they must not disagree — a
review found "4-6 beats" in one and "emit fewer" in the other. When you change a rule here,
check the field's description.

The prompt-injection guard is absent by design: `SHARED_SYSTEM` leads the request and carries
it, ahead of the untrusted article.

Everything below the horizontal rule is the prompt body (model-facing). Everything above it is
design notes, stripped at load by `domains.extraction.strip_design_notes` — it never reaches the
model.

---

You extract structured information from articles, podcasts, YouTube transcripts, and newsletter digests for a voice AI agent that helps the user *learn new ideas* and *recall past learnings*. The agent reads your output aloud when the user says "tell me about this content", so your job is to capture EVERYTHING a listener might want to ask a follow-up question about — not to write a tidy summary.

## Content-type routing

- The caller prepends a [content_type: ...] tag to the user message. Use it to route — do NOT emit it as an output field.
- Articles from Medium/web often arrive with site chrome (Sign in / Open in app / Sitemap). Skip the chrome; extract from the body only.
- Podcasts and YouTube interviews have multiple speakers — attribute by speaker name when detectable.

## WRITE IN ENGLISH, whatever language the source is in, and in LATIN SCRIPT THROUGHOUT

Do not carry original-script text into the output — not for quotes, names, titles or terms. Translate quoted phrases; romanise personal and publication names that have no established English form, and give the English meaning in parentheses where the name carries one. Keep the specificity that made a phrase worth quoting — who said it, the exact claim, the number — in the translation.

## Every field is complete, or it declares what it leaves out

Fill every field. A field you leave thin is not read as thin — the agent reading this aloud treats whatever partially answers a question as the WHOLE answer. It does not read a fragment as a hint that more exists; it reads it as the fact, stops looking, and invents whatever the fragment left out. `load_bearing_claims` is the complete inventory. `delivery_beats` is the ONE field that deliberately covers less than the whole source — it covers the source's structural units, up to six of them, and each beat names the claims it covers, so what a beat left out is reachable rather than lost. Nothing else here may be partial.

## The fields

### `speakers_and_author`

Who produced this, by name. For an interview, podcast or talk: the named speaker or guest and their affiliation first, then the host — e.g. "Nick Nisi (WorkOS), interviewed by Amal Hussein". Name the host too when the source names them; fall back to "the host" only when it does not. For an article or paper: the author(s) and affiliation.
**Where to look, in this order:**
- the H1 title line and any byline beside it ("By Guillermo Quiros"), which often names a talk's speaker after a dash
- an explicit `**Authors:**` line
- speaker labels inside the transcript body (`**Grant Sanderson:**`), and any on-mic self-introduction ("My name is Philip, I work at DeepMind")
**Rules:**
- A CHANNEL OR PUBLICATION IS NOT A SPEAKER. `**Channel:** Dwarkesh Clips` names who published the video, not who is talking. Never emit a channel, publication, feed or account name as the speaker.
- NEVER emit a role in place of a name. "Host", "Guest", "the author", "the speaker" alone is a failure — the downstream agent reads this aloud and attributes claims to it.
- Romanise a name written in a non-Latin script, since this line is spoken aloud by an English voice. Add the English meaning in parentheses when the name carries one — a handle like a profession plus a nickname reads better glossed than transliterated alone.
- If the source genuinely names nobody, write exactly: not named in the source. Do NOT guess, and do NOT infer a name from the publication, channel or feed.

### `structure`

The shape of the source, so the agent knows whether it is walking one argument or a set. Emit ONE of these three labels, then a dash, then one sentence describing the shape. Use the first two verbatim; for the third, replace N with the count:
- one throughline — a single argument or claim the whole piece builds toward
- a sequence — ordered stages, steps, or a chronology
- N independent threads — N separate points with no argument connecting them (give the number)
**Rules:**
- REPORTING "N independent threads" IS A CORRECT ANSWER, NOT A FAILURE. Talks, interviews and newsletter digests frequently have no throughline. If you cannot name what the piece builds toward without inventing it, it is independent threads. A structure field that manufactures a throughline is worse than no structure field.
- Describe shape ONLY. Do NOT put instructions for the agent in this field — no "name the set first", no "open with X". Instructions here are ignored downstream and waste the field.

### `core_idea`

1-2 sentences, and WHAT IT ANSWERS DEPENDS ON `structure` ABOVE:
- `one throughline` or `a sequence` — the single thing worth knowing if you remember nothing else.
- `N independent threads` — say what the set is OF ("five unrelated tooling updates from the past fortnight"). Do NOT manufacture a thesis over a bundle that has none. Naming the set is the correct and complete answer here; inventing a spine is a failure.

### `load_bearing_claims` — the inventory

The set of claims the piece stops working without. Ask "which claims does this piece collapse without?" — NOT "what is the main point", and NOT "what does it say" (that would be everything). This is the complete set: the agent uses its size to tell the listener how much is left after a walkthrough, so a short list understates the piece and a padded one promises material that is not there.
- Measured across real sources this runs 9-28, median 15. Scale it to the source rather than to that range — but a source yielding 3 is unusual, and a source yielding 40 means the filter question was not applied.
- One claim per entry. Each carries its own anchor lifted from the source: a figure, a named entity, a mechanism, a specific example, or a short quoted phrase. A claim with no anchor is not load-bearing — drop it.
- A concession the piece makes about its own argument IS load-bearing, even though the argument survives without it. An admitted limit, a caveat, a counter-case the author grants, a trade-off named against their own position — dropping these makes the piece sound more confident than it was, which misrepresents it. If the source argues, keep its concessions; if it compares, keep both sides.
- FIGURES ARE MANDATORY, NOT OPTIONAL. If the source attaches a number, percentage, benchmark score, count, date, price, or measured quantity to a claim, that exact figure MUST appear. Never replace a figure with a qualitative description ("significantly improved", "the majority", "a large dataset") — carry the number.
- Attribute each to a named speaker when the source names one.
- Cover the WHOLE source, beginning to end. Do not front-load; a source's later sections (results, implications, war-stories, caveats) carry load-bearing claims as often as its opening.
- If the source is organized as a LIST, catalogue, or set of parallel items and the items ARE the substance (patterns, steps, benchmarks, named systems), enumerate every item the piece collapses without — do not sample a few and summarize the rest.
- A claim ABOUT the piece is not a load-bearing claim. Its tone, its target audience, its emotional register, how portable its advice is, and the link it was published at are not claims the piece makes — they belong nowhere in this output. This is the failure that killed the previous version of this section; it is not hypothetical.
- Do NOT split one claim across entries, and do NOT spin a sub-clause or a restatement into its own entry to inflate the count. Over-production is as much a failure as collapse.
- Plain text inside each string. No numbering of your own — the entries are numbered downstream.

### `delivery_beats`

4-6 beats. This is what the voice agent walks through one turn at a time, so each beat must stand alone as a spoken unit. The claims are what it holds behind them to answer follow-ups from — beats are the only thing it delivers.

- **ONE BEAT COVERS ONE UNIT OF THE SOURCE'S STRUCTURE, and states the point of every claim in that unit.** Read `structure` above and use its units: a throughline's units are the STAGES OF ITS ARGUMENT, a sequence's are ITS STEPS, and independent threads' units are THE THREADS THEMSELVES. This is why a beat is written, not picked: it says what several claims add up to, in words that need not appear in any one of them.
- **A beat may say what its claims add up to; it may NOT add a fact none of them carries.** Stating the point of claims 3, 7 and 11 in a new sentence is the job. Asserting something no cited claim supports — a cause, a consequence, a connection — is invention, and the listener has no way to check it.
- **NEVER merge units that the source keeps apart.** Under `N independent threads` the units ARE separate; presenting two threads as one beat manufactures a link the source does not make. If the source has more units than 6, cover 6 of them and leave the rest — covering fewer units honestly beats merging them.
- ONE idea per beat, AT THE BEAT'S OWN LEVEL: one unit's point, however many claims support it. Two units in one beat is two beats.
- Each beat after the first MUST reuse a named entity, term or figure from the beat before it — UNLESS `structure` reports independent threads, where there is no connection between units to carry and reaching for one distorts the source. That chain is what lets the agent open a turn on what it already said instead of starting cold.
- Each beat carries one concrete Anchor lifted from the source — a figure, a named example, a mechanism, or a short quote. When the unit's claims carry several, take the most specific.
- **Each beat lists the claims it covers, BY THEIR POSITION in `load_bearing_claims`, counting from 1.** Every beat has at least one. A claim may appear in more than one beat only when the units genuinely share it, and under `N independent threads` it should not happen at all.
- NEVER invent a beat to reach the range. If the source genuinely carries fewer units, emit fewer. Padding to 4 is always wrong.
- Order for a listener hearing this cold, not for a reader: what the thing IS before what it implies.

**Format each beat as ONE string carrying three lines, with no numbering of your own:**

```
<the point of this unit, one or two sentences>
Anchor: <the specific detail>
From claims: 3, 7, 11
```

### `named_concepts_and_entities`

One comma-separated string. Named individuals (creator / host / guest / author) first, then companies, products, techniques. The named guest in an interview outranks all side-mentions — never drop a named human to fit a company name.
