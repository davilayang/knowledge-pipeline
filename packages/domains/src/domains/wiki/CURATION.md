# Wiki entity curation runbook (reject + merge)

The wiki synthesiser auto-mints an entity per distinct named thing. Two failure
modes need a human in the loop:

- **Noise** — site chrome / mis-extractions become pages (`Cookie Policy`,
  `Related Posts`). → **reject** (delete + tombstone).
- **Duplicates** — the same real entity minted under two surface forms
  (`Tree of Thoughts` vs `Tree-of-Thoughts`, `Code Reviews` vs `Code Review`).
  The resolver never auto-merges (a false merge is destructive), so it mints a
  safe false-split and records a hint. → **merge** (fold the duplicate into a
  survivor).

Both CLIs read/operate on `wiki.db` + the `data/wiki/*.md` pages:

| CLI | Package | What it does |
|---|---|---|
| `wiki-reject` | `domains` | delete a noise entity + tombstone its name + every alias |
| `wiki-merge` | `domains` | fold a duplicate ("drop") into a survivor ("keep"); re-points claims + aliases, aliases drop's name onto keep, deletes drop |

## How to run it — laptop-driven, against prod data

You drive the flow **from the laptop** with a Claude session helping. The split
that keeps it both convenient and safe:

- **Writes happen in-cluster.** The destructive `wiki-reject` runs via
  `ssh hcloud … docker exec …` against prod's live files. *Don't* pull the DB,
  mutate it locally, and push it back — that clobbers any synthesis writes made
  in between.

**Never run a write during the 06:00 UTC synthesis window** — SQLite is
single-writer; a concurrent destructive write can corrupt a read-resolve-write
tick.

`data/` and `backups/` are host-bind-mounted, so the container's `/app/data` and
the host's `~/knowledge-pipeline/data` are the same files; `<dagster-code>` is
the user-code container name.

## The loop

Reject noise in-cluster:

```
ssh hcloud 'docker exec -w /app <dagster-code> uv run wiki-reject \
  --db data/wiki.db --wiki-dir data/wiki --name "Cookie Policy" \
  --category chrome --reason "site boilerplate"'
```

Take a timestamped rollback snapshot first if rejecting a batch. Loose files at
the `backups/` top level survive the daily-backup prune (it only touches date
dirs), so delete them yourself once the reject has stuck:

```
ssh hcloud 'cd knowledge-pipeline && TS=$(date +%Y%m%d-%H%M%S) && \
  sqlite3 data/wiki.db ".backup backups/wiki-prereject-$TS.db" && \
  tar czf backups/wiki-prereject-$TS.tgz -C data wiki && \
  echo "snapshot: backups/wiki-prereject-$TS.{db,tgz}"'
```

**Roll back** a bad batch (synthesis stopped) from a snapshot:

```
ssh hcloud 'cd knowledge-pipeline && cp backups/wiki-prereject-<TS>.db data/wiki.db && \
  rm -rf data/wiki && tar xzf backups/wiki-prereject-<TS>.tgz -C data'
```

### Local rehearsal (no prod)

Validate the flow on a throwaway copy first: point `wiki-reject` at a local
`data/wiki.db` + `data/wiki` and drop the `ssh hcloud` / `docker exec` wrappers
(snapshot to a local `wiki-prereject-$TS.{db,tgz}`). This is the safe way to
test a reject before touching prod.

### Effect on the next synthesis run

- **Reject:** the canonical name + every alias land in `rejected_entities`;
  synthesis drops any candidate matching them before minting, so the entity
  can't come back under a **known** surface form. A brand-new synonym never seen
  as an alias can still re-mint once — reject it again (name-keyed suppression
  can't anticipate an unseen synonym).
- `index.md` and `_index/resolve.json` are not rewritten by the CLI, but the
  `synthesize_wiki` `build_index` asset regenerates both from `wiki.db` on each
  tick — so a merge/reject that changes the entity set is reflected in the TOC
  and the alias→entity resolution sidecar at the next synthesis run.

## Merge duplicates

Candidate pairs come from the offline dedup search
(`evals.wiki_dedup.openai_candidates`) — an embedding pass (claim-weighted
cosine) unioned with a lexical name-only pass (`domains.wiki.dedup.find_name_candidates`,
which recovers a claim-rich entity's thin name-twin the embedding pass misses).
An agent JUDGEs the pairs; a human CONFIRMs; then merge in-cluster:

```
ssh hcloud 'docker exec -w /app <dagster-code> uv run wiki-merge \
  --db data/wiki.db --wiki-dir data/wiki \
  --keep e_<survivor> --drop e_<dup> --backup'
```

`--dry-run` reports the plan and rolls back (rehearse on a pulled copy).
`--no-alias` keeps two homonyms separate (drop's name mints fresh next time).

**Conventions for the CONFIRM step:**

- **Survivor = the canonical form, not the higher claim count.** `merge_entities`
  re-points every claim from drop onto keep, so nothing is lost by keeping a
  lower-claim entity. Pick the survivor by naming convention; the drop's spelling
  becomes an alias, so it still resolves and future mentions of it auto-route to
  the survivor.
- **Prefer the SINGULAR form** for concept/role duplicates — `Code review` not
  `Code reviews`, `Analytics engineer` not `analytics engineers`, `Multi-agent
  framework` not `frameworks`. This is the ontology/Wikipedia convention; it only
  changes the page title + filename. (Keep the plural only when it is the
  established proper name.)
- **HOLD — never merge** version/size variants (`Opus 4.5` vs `4.7`, `Qwen 7B`
  vs `72B`), family-vs-specific (`Claude Sonnet` vs `Claude-4-Sonnet`), or names
  that merely look alike but are distinct concepts (`Unit Testing` vs `UI
  Testing`, `10-K` vs `10-Q filings`, `DeepSeek-V3` vs `DeepSeek-VL`). The
  candidate search auto-drops the digit-differ variants
  (`find_name_candidates`'s digit guard), but the human gate is the backstop for
  the rest.

Batch merges need a rollback snapshot first (same command as the reject batch
below) and a render sweep after — a merge touches no source watermark, so the
scheduled incremental sweep won't redraw the survivor. Re-run
`synthesize_wiki/render_pages` + `build_index` (or call `render_entity_pages` +
`build_wiki_index` directly) to redraw pages and rebuild `resolve.json`.

## Rebuild carve-out (the option-(b) tax)

`rejected_entities` is the one **authored**, non-regenerable table in `wiki.db`.
A plain backup-restore preserves it, but a *from-empty schema rebuild*
(`drop wiki.db → re-synthesise`) drops it. Before such a rebuild:

```
sqlite3 data/wiki.db ".dump rejected_entities" > /tmp/curation.sql
#   … drop + re-synthesise on the new schema …
sqlite3 data/wiki.db < /tmp/curation.sql      # reseed the denylist
```
</content>
</invoke>
