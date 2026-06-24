# Wiki entity curation runbook (merge / reject / dedup)

The wiki synthesiser auto-mints an entity per distinct named thing. Two failure
modes need a human in the loop:

- **Duplicates** — two entities for one real thing (`Claude Max` ≡ `Max plan`).
  The in-synthesis fuzzy matcher (difflib ≥ 0.85) can't catch names that diverge
  while meaning converges. → **merge**.
- **Noise** — site chrome / mis-extractions become pages (`Cookie Policy`,
  `Related Posts`). → **reject** (delete + tombstone).

Three CLIs, all read/operate on `wiki.db` + the `data/wiki/*.md` pages:

| CLI | Package | What it does |
|---|---|---|
| `wiki-dedup-candidates` | `evals` | READ-ONLY: embed `name + summary`, print near-dup pairs as JSON |
| `wiki-merge` | `domains` | fold one entity into another (re-point ledgers, alias the dropped name, delete its page) |
| `wiki-reject` | `domains` | delete a noise entity + tombstone its name + every alias |

## How to run it — laptop-driven, against prod data

You drive the whole flow **from the laptop** with a Claude session helping. The
split that keeps it both convenient and safe:

- **Reads are local + free.** Pull prod's `wiki.db` + `wiki/` down, generate
  candidates locally against a local llama.cpp embedder (no OpenAI cost), and
  Claude judges the `candidates.json` on the laptop.
- **Writes happen in-cluster.** The destructive `wiki-merge` / `wiki-reject` run
  via `ssh hcloud … docker exec …` against prod's live files. Entity ids are
  stable surrogates, so a slightly-stale pulled copy is fine for judging.
  *Don't* pull the DB, mutate it locally, and push it back — that clobbers any
  synthesis writes made in between.

**Never run a write during the 06:00 UTC synthesis window** — SQLite is
single-writer; a concurrent destructive write can corrupt a read-resolve-write
tick.

`data/` and `backups/` are host-bind-mounted, so the container's `/app/data` and
the host's `~/knowledge-pipeline/data` are the same files; `<dagster-code>` is
the user-code container name.

## The loop

```
0. PULL      prod wiki.db + pages to the laptop (read-only candidate gen):
               rsync -az hcloud:knowledge-pipeline/data/wiki.db ./prod-wiki/wiki.db
               rsync -az hcloud:knowledge-pipeline/data/wiki/   ./prod-wiki/wiki/

1. READ      candidates locally, free (llama.cpp embeddings):
               llama-server -hf nomic-ai/nomic-embed-text-v1.5-GGUF \
                 --embeddings --pooling mean --port 8080 &
               uv run wiki-dedup-candidates --db ./prod-wiki/wiki.db --wiki-dir ./prod-wiki/wiki \
                 --embed-base-url http://localhost:8080/v1 \
                 --embedding-model nomic-embed-text-v1.5 --embed-prefix "search_document: " \
                 --threshold 0.8 > candidates.json
             (Drop the --embed-* flags to use OpenAI instead — see "Embedding backend".)

2. JUDGE     Claude reads candidates.json: for each pair, keep vs drop, or skip
             ("not a dup"). Names + summaries are in the JSON — no DB lookups.

3. CONFIRM   you approve each merge + pick --no-alias for homonyms (a dropped
             name that could later mean something else — a future "Max plan"
             telecom tier shouldn't route to Claude Max).

4. SNAPSHOT  timestamped pre-merge rollback point on prod. Loose files at the
             backups/ top level — the daily-backup prune only touches date dirs,
             so these survive (delete them yourself once the merge has stuck):
               ssh hcloud 'cd knowledge-pipeline && TS=$(date +%Y%m%d-%H%M%S) && \
                 sqlite3 data/wiki.db ".backup backups/wiki-premerge-$TS.db" && \
                 tar czf backups/wiki-premerge-$TS.tgz -C data wiki && \
                 echo "snapshot: backups/wiki-premerge-$TS.{db,tgz}"'

5. MERGE     in-cluster, per approved pair (Claude scripts the loop; --dry-run first):
               ssh hcloud 'docker exec -w /app <dagster-code> uv run wiki-merge \
                 --db data/wiki.db --wiki-dir data/wiki \
                 --keep e_<survivor> --drop e_<dup> [--no-alias]'
```

Reject noise the same way (no candidate step):

```
ssh hcloud 'docker exec -w /app <dagster-code> uv run wiki-reject \
  --db data/wiki.db --wiki-dir data/wiki --name "Cookie Policy" \
  --category chrome --reason "site boilerplate"'
```

**Roll back** a bad batch (synthesis stopped) from a step-4 snapshot:

```
ssh hcloud 'cd knowledge-pipeline && cp backups/wiki-premerge-<TS>.db data/wiki.db && \
  rm -rf data/wiki && tar xzf backups/wiki-premerge-<TS>.tgz -C data'
```

### Local rehearsal (no prod)

Validate the flow on a throwaway copy first: point every command at a local
`data/wiki.db` + `data/wiki` and drop the `ssh hcloud` / `docker exec` wrappers
(snapshot to a local `wiki-premerge-$TS.{db,tgz}`). This is the safe way to test
a merge before touching prod.

### Effect on the next synthesis run

- **Merge:** the dropped name is written as an alias of the survivor (unless
  `--no-alias`), so the next article mentioning it folds in instead of re-minting
  the dup. The survivor's page **prose** lags until a new source re-synthesises
  it; its frontmatter (aliases / num_sources / sources / related) is correct
  immediately.
- **Reject:** the canonical name + every alias land in `rejected_entities`;
  synthesis drops any candidate matching them before minting, so the entity
  can't come back under a **known** surface form. A brand-new synonym never seen
  as an alias can still re-mint once — reject it again (name-keyed suppression
  can't anticipate an unseen synonym).
- `index.md` and `_index/aliases.json` regenerate on the next `synthesize_wiki`
  tick; they're not rewritten by the CLIs.

## Embedding backend (`wiki-dedup-candidates`)

Candidate generation only needs a *similarity heuristic* a human then judges —
it does NOT need to match the production Chroma embedding space, so a local model
is fine (and free).

- **Local (default in this runbook):** `llama-server --embeddings` exposes an
  OpenAI-compatible `/v1/embeddings`, so the same CLI points at it via
  `--embed-base-url` — no extra package. `--embed-prefix "search_document: "` is
  required for good nomic-embed quality (its task prefix). `--embedding-dims` is
  ignored here — llama.cpp returns the model's native, already-L2-normalized dim
  and rejects the `dimensions` param; `--pooling` must not be `none`.
- **OpenAI fallback:** drop the `--embed-*` flags (needs `OPENAI_API_KEY`; ~150
  short texts costs cents). The two aren't exclusive — if a local pass misses a
  pair you expected, re-run without `--embed-base-url` to use OpenAI's stronger
  model. Same corpus, one flag.

## Rebuild carve-out (the option-(b) tax)

`rejected_entities` is the one **authored**, non-regenerable table in `wiki.db`.
A plain backup-restore preserves it, but a *from-empty schema rebuild*
(`drop wiki.db → re-synthesise`) drops it. Before such a rebuild:

```
sqlite3 data/wiki.db ".dump rejected_entities" > /tmp/curation.sql
#   … drop + re-synthesise on the new schema …
sqlite3 data/wiki.db < /tmp/curation.sql      # reseed the denylist
```

Merge aliases are re-derived by re-running the dedup loop, not replayed.
