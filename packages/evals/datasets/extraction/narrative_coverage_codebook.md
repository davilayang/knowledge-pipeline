# Codebook — drafting `gold_threads` for narrative-coverage fixtures

**This document is handed to a labeler, so it contains instructions only.** No labels for the
fixture being labelled, no resulting scores, no record of how earlier rounds went — any of that
would anchor the labeler and cost the blindness the gold depends on. The calibration section
below does quote threads from two *other* fixtures; that is deliberate and is what calibrates
specificity. What was actually produced, and how to read
a number off it, lives in the `narrative_coverage_gold.jsonl` section of
[`README.md`](README.md), which is never given to a labeler.

You are labelling a **gold dataset**. Your output is the reference answer that systems will
later be scored against. You have NOT been shown any system's output and you must not guess at
one — write what the source contains, independently.

## What a thread is

From the dataset's own header definition:

> `gold_threads` = **distinct-follow-up-grain threads**, drafted by chunked read,
> blind-scored, FN-heavy asymmetric loss.

Operationally: **a thread is one point in the source that a listener could ask a SEPARATE
follow-up question about.** A claim, finding, argument, method, result, story beat, comparison,
objection, statistic, or framing.

## Grain — the decisive test

This is where labelers disagree most, so the rule is functional, not a count.

**A point together with its supporting reason, example, or figure is ONE thread.** Split into
two only if a listener could ask about the second WITHOUT having heard the first.

Worked contrast, from a real talk about building software agents:

- **ONE thread** — *"errors are just inputs: a failure mid-run should be fed back to the model
  rather than restarting, because a 15-minute agent run loses its accumulated context on
  restart."* The cost rationale is the *reason for* the principle. A listener asks about this
  once.
- **TWO threads** — *"the speaker works on Gemini agents at DeepMind"* and *"the talk walks
  five examples."* Independent facts; either can be asked about with no knowledge of the other.

Further grain rules:

- If the source is a **list or set of parallel items** (patterns, steps, questions, named
  systems), each item is its own thread — but an item's own supporting detail stays inside it.
- A **restatement** of a point already listed is not a new thread. A source that states a point
  early and repeats it in a conclusion yields one thread.
- A **summary or recap section** that re-states points already covered earlier adds no threads.
  Only genuinely new material in a closing section earns one.

**Do not target a count.** Density follows content, not length. In this dataset the drafted
density ranges from about one thread per 450 characters (a dense argument essay) to one per
13,000 (an arXiv survey whose bulk is method detail). Both are correct for their source.

**Character counts are not a density guide for non-Latin scripts.** A Chinese character carries
several times the content of a Latin one, so a short CJK source can legitimately carry many more
threads than its character count suggests. Judge by distinct points, never by length.

## Anchors are mandatory

Every thread must carry at least one concrete anchor lifted from the source: a named entity, a
figure, a mechanism, a specific example, or a short quoted phrase.

**Figures especially.** If the source attaches a number, percentage, date, price, or measured
quantity to a point, that exact figure must appear in the thread. Never replace a figure with a
qualitative description.

## Format

One line per thread: `<id>: <compressed description with its anchors>`

- `id` is a letter (A, B, C…) or number (1, 2, 3…) — either is fine, be consistent.
- The description is compressed, not prose. Pack in the named entities and figures.
- Write in **English**, even when the source is in another language. Preserve distinctive
  original-language terms or quoted phrases inline with a gloss where they carry meaning.

## Calibration — real threads from two existing fixtures in this same dataset

From a 16,156-char YouTube talk (11 threads):

```
A: AI turns non-builder GTM people into builders (bicycle-for-the-mind analogy)
B: hallucination reframed as a trust problem; AI gives confident wrong answers
C: main thesis — manage your agents like other humans
D: use commander's intent when prompting; don't tell the agent to improve itself
F: radiant librarian = just-in-time memory disambiguating terms (fiscal year Feb-Apr)
I: multi-touch attribution took 2 years to solve
J: agent tiers / friends-don't-let-friends-use-bad-harnesses (Slackbot MCP too weak, pre-subscription margin)
```

From a 16,377-char listicle (11 threads — note one thread per list item):

```
1: thesis — wrong pattern choice causes costly rewrites; match pattern to problem, not best tech
4: Streaming — milliseconds; Kafka->Flink; offset commit; use cases (fraud, trading)
6: Kappa — eliminate batch (Jay Kreps); replay log; Kafka infinite retention; expensive replay
11: decision rules (freshness->pattern; logic-evolution->Kappa vs Lambda; discipline to pick)
```

These are examples of the **specificity** to aim for — how much named detail each line carries.
They are **not** a density target. Do not count their threads-per-character and try to match it:
density is a property of the source, and these two happen to be long prose. If your count feels
high or low for your source, check it against the grain test above, which is the arbiter.

## Tie-breaks

- **Unsure whether something is one thread or two?** Split it. The loss is FN-heavy — a missed
  thread costs more than an extra one.
- **Unsure whether a minor point deserves a thread?** Include it. Same asymmetry.
- **A point stated in the source AND restated in a conclusion** — one thread, not two.
- **Contested figures** (source cites a claim's number and then a contradicting number) — one
  thread carrying both numbers, since a listener asks about the contradiction as one question.

## Output

Return ONLY the thread list, one per line, in the format above. No preamble, no commentary, no
count summary.
