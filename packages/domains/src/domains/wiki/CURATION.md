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

## The loop (cluster → judge → confirm → merge)

**Rehearse on the LOCAL copy first** (`data/wiki.db`), then repeat against prod
in-cluster once the calls are validated. The human gates every merge — the
candidate generator only proposes.

```
1. READ      uv run wiki-dedup-candidates --db data/wiki.db --wiki-dir data/wiki \
               --threshold 0.8 > candidates.json
             (embeds each entity's name+summary; OpenAI by default. To run it
              free + local, see "Embedding backend" below.)
2. JUDGE     review candidates.json (a Claude session is good at this): for each
             pair decide keep vs drop, or "not a dup" (skip). Names + summaries
             are in the JSON so no DB lookups are needed.
3. CONFIRM   you approve each merge; aliasing the dropped name onto the survivor
             is the default. Pass --no-alias as the homonym escape hatch: use it
             when the dropped name could later mean something else (a future
             "Max plan" telecom tier shouldn't route to Claude Max).
4. SNAPSHOT  pre-merge rollback point (merges are destructive):
               sqlite3 data/wiki.db ".backup data/wiki.bak.db"
               tar czf data/wiki.bak.tgz -C data wiki
5. MERGE     per approved pair (loop for multi-drop clusters):
               uv run wiki-merge --db data/wiki.db --wiki-dir data/wiki \
                 --keep e_<survivor> --drop e_<dup> [--no-alias]
             (--dry-run first to preview.)
```

Reject noise the same way, no candidate step needed:

```
uv run wiki-reject --db data/wiki.db --wiki-dir data/wiki --name "Cookie Policy" \
  --category chrome --reason "site boilerplate"
```

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
it does NOT need to match the production Chroma embedding space. So it runs on
either backend; the rest of the loop (judge / merge / reject) is identical.

**OpenAI (default).** Needs `OPENAI_API_KEY`; ~150 short texts costs cents:

```
uv run wiki-dedup-candidates --db data/wiki.db --wiki-dir data/wiki
```

**Local (free) via llama.cpp.** `llama-server` serves an OpenAI-compatible
`/v1/embeddings`, so the same CLI points at it — no extra package. Start the
server (it pulls the GGUF from HuggingFace on first run), then pass
`--embed-base-url`:

```
llama-server -hf nomic-ai/nomic-embed-text-v1.5-GGUF --embeddings --pooling mean --port 8080

uv run wiki-dedup-candidates --db data/wiki.db --wiki-dir data/wiki \
  --embed-base-url http://localhost:8080/v1 \
  --embedding-model nomic-embed-text-v1.5 \
  --embed-prefix "search_document: "
```

Notes:
- `--embed-prefix "search_document: "` is required for good nomic-embed quality
  (the model's task prefix); other models may not need it.
- `--embedding-dims` is ignored with `--embed-base-url` — llama.cpp's
  `/v1/embeddings` rejects the `dimensions` param and returns the model's native
  dim (already L2-normalized). `--pooling` must NOT be `none` for `/v1/embeddings`.
- The two backends aren't exclusive: if a local pass misses a pair you expected,
  re-run the same command **without** `--embed-base-url` to fall back to OpenAI's
  stronger model. Same corpus, one flag.

## Running against prod

`wiki.db` and the `.md` files live inside the `dagster-code` container. Run the
candidate READ from the laptop against a recent snapshot or via Datasette, but
execute the destructive steps in-cluster:

```
ssh hcloud
docker exec -w /app <dagster-code> uv run wiki-merge --db data/wiki.db \
  --wiki-dir data/wiki --keep e_… --drop e_…
```

**Do NOT run a merge/reject during the synthesis window** — SQLite is
single-writer and synthesis does read-resolve-write; a concurrent destructive
write can corrupt the tick. The primitives assume a single writer.

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
