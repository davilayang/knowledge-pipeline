# Search datasets: `probes.jsonl`, `gold.jsonl`, `snapshot.manifest`

Recall probes for the search service (`POST /v1/search`): the questions, their
graded gold, and the identity of the frozen snapshot the gold was graded on.
Labelling rules are in [`codebook.md`](codebook.md).

## What it decides

Whether a search change finds more of what was asked for, per caller (`voice`,
`mcp`), and whether a port of the search keeps today's results. The headline is
recall over grade-2 needs at the caller's fixed budget; precision (items that
credit any need) is the noise cost; F2 matters only when two arms hand over
different numbers of items; `primary found` counts probes whose primary piece
was returned. A report, not a gate. Entry point:
`uv run eval-search-score --run <run.json>`.

It does not decide anything about notes, briefs or wiki pages as answers: gold
targets corpus items (saved items in corpus.db, whatever their medium) and
sessions only, so those lanes score only through the corpus items they lead to.

## Provenance

Asks are the user's own questions, cleaned of typos with meaning kept, written
before any search component was tuned on them. Gold was graded by the user on
the snapshot, with the codebook's rules; `primary` is the user's label only.
Every probe here has been looked at while shaping search, so this is **tuning
material**: a number on it shows the fit, not the gain. Claims of improvement
need held-out asks, which are kept outside every repository.

## How to read a number off it

- A run is comparable to another only on the same snapshot digest and dataset
  version; the CLI refuses a snapshot whose digest differs from the manifest.
- Session credit is generous: a session hit credits every gold piece fetched in
  that session, so a roundup session can credit several needs at once.
- An item outside the gold is unjudged, not irrelevant, until graded: a
  component that surfaces new pieces needs them graded blind before its
  precision is claimed.
- Credit counts once per need, so a run returning both copies of a duplicated
  corpus item finds one need, not two.

## Identity and safe changes

- `gold_id` and `probe_id` are permanent; a regraded need keeps its id. Uris are
  `kos://piece/<kind>/<id>` with the id encoded by `quote(id, safe=":")`.
- Changing the asks, a grade, or the accepted uris bumps `dataset_version` in
  both headers. A schema change bumps `schema_version`.
- Negative probes (`negative`: `plain` or `near`) carry no gold. Within a
  probe, a corpus item belongs to one need; a session may belong to several.
- Validate a change by running the CLI on a saved run: it checks schema and
  dataset versions, uri resolution on the snapshot, negatives, duplicates and the snapshot digest
  before scoring.

## Refresh policy

The snapshot (`data/eval_snapshots/<date>/`: `corpus.db`, `sessions.db`, note,
brief and wiki files) is gitignored and never committed; only its per-lane
digests are. On a refresh, keep every `gold_id`, check each uri still resolves
and its content still matches, and review changed, aliased and deleted pieces
and the negatives by hand. Gold and snapshot are versioned together; missing
gold is never dropped silently.

The section headings follow the convention in [`../README.md`](../README.md#what-a-dataset-section-records).
