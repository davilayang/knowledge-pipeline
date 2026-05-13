# Personal Knowledge OS — project framing

> **Note:** Edit this file to update the framing; do not duplicate the content
> in `CLAUDE.md`. This file applies to `knowledge-pipeline`; a per-repo copy
> lives in `newsletter-assistant`. Sync only when the core concept changes
> (rare) — per-repo divergence in Section 3 (trajectory) is by design.

## Project Overview

**A personal knowledge OS — voice-first, designed so everything you consume
either becomes something you built or something you'll still know precisely
months later.**

This project (`knowledge-pipeline`) is the async ingestion and synthesis layer.
Its sibling project (`newsletter-assistant`) is
the real-time agent layer (voice agent, MCP server, web UI). Together they
form one system: consumption + thinking + retention + retrieval, with optional
detour into applied work via the research panel (Claude/Gemini CLI with
codebase access — owned by newsletter-assistant).

This repo's contribution: turn raw consumed content (articles, sessions,
research output, notes) into the durable second-brain substrate — periodically
backed up, indexed for semantic recall, and synthesised into entity wiki pages
that accumulate across content over time.

The user is a single builder who learns by doing, consumes a lot of high-signal
content, and wants their reading life to compound into projects shipped or
knowledge that stays.

## How compounding happens — two exit ramps

Every piece of consumed content should land in one of two durable forms (or both):

**Ramp 1 — Apply (hands-on topics).** Read a coding pattern, an architecture
writeup, a new tool — open the research panel (in newsletter-assistant), point
Claude/Gemini at the relevant codebase, try the idea immediately. The artifact
is code shipped or experiment run.

**Ramp 2 — Retain (pure-interest or future-relevance topics).** Read an RNA
biology paper, a podcast on cultural trends, a research talk on something
tangential — the artifact is a structured note (newsletter-assistant), an
**entity wiki page that updates over time** (synthesised in this repo by the
`synthesize_wiki` pipeline), or a precise-enough memory that can be recalled
with vector + entity-graph search months later.

**This repo owns Ramp 2's durable substrate.** Indexing pipeline turns articles
into Chroma vectors used by newsletter-assistant's `recall` tool. Wiki
synthesis pipeline turns raw content into accumulating entity pages. Backup
pipeline keeps the SQLite/Chroma sources snapshot-recoverable. Retrieval eval
harness measures how well the substrate actually delivers recall.

What gets indexed/synthesised here determines what newsletter-assistant's voice
agent can pull back into a session weeks later. Abstractions evaporate; named
people, specific numbers, and concrete examples persist — so the wiki
synthesis prompt, the chunking strategy, and the embedding choice all matter
to the final user-facing recall quality.

## Where we are on the journey

This framing describes the destination. Reality today in this repo:

- **Working today:** Backup pipelines for raw_store / sessions / research
  (date-partitioned, scheduled). Wiki synthesis pipeline (first version,
  date-partitioned, scheduled). Domain layer covering raw_store / sessions /
  research / notes — sources are interchangeable in ingestion loops.
- **Built, consumer-bridge designed 2026-05-13:** Wiki synthesis. Wiki
  pages accumulate in `data/wiki/`; newsletter-assistant doesn't read
  them yet, but the cross-repo bridge design landed (hub
  architecture.md §8.3, ADR-013). Producer-side execution is an
  **additive** frontmatter contract — `summary`, `aliases`,
  `num_sources` fields plus a `data/wiki/_index/aliases.json` sidecar
  written at end of each `synthesize_wiki` tick. See this repo's
  `ai-plannings/` for the producer-side execution stub. Consumer
  wiring lands in Wave 6 on the newsletter-assistant side; producer
  contract additions are unblocked today.
- **In flight:** Index pipeline (raw_store → Chroma) — the path that turns
  fetched articles into vectors used by newsletter-assistant's `recall` tool.
  Retriever and generation evals — first cuts of the eval harness exist
  (Recall@5 / MRR@10 / nDCG@10 over a 166-pair labelled set), but iteration
  on what they measure and how the results actually drive production
  retrieval is ongoing.
- **Earlier design, not yet merged into the pipeline plan:** A workbench
  surface for testing retrieval-strategy scores standalone. Worth revisiting
  once the index pipeline and evals stabilise — the design intent (try
  strategies → measure → promote winners) is sound but the integration with
  production hasn't been designed yet.
- **Cross-corpus retrieval still aspirational:** Sessions, research, and
  raw_store sources are read at the domain layer, but ranking across them
  coherently (weighting "what's in your reading history" vs "your past
  conversations" vs "what you noted") is not built.

PRs that ship the producer-side wiki-bridge contract additions
(unblocked today — see `ai-plannings/`), push the index pipeline +
evals toward driving production retrieval, or improve cross-corpus
retrieval are higher-leverage than PRs that polish the already-
working backup or synthesis layers.
