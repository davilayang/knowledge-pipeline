# `backup_readings` runbook

Daily-partitioned snapshot of the newsletter-assistant SQLite databases
(`raw_store.db`, `sessions.db`, `research.db`) plus a gzip-tar archive of the
flat-file `notes/` dir, with Google Drive offload via rclone and a
healthchecks.io ping that turns silence (cron didn't fire, daemon died) into
a loud alert.

## DAG (per partition)

Failure cascade — what blocks what when a step fails:

```
snapshot_raw_store         ─┐
snapshot_sessions          ─┤
snapshot_research          ─┤
snapshot_notes             ─┼─→ verify_* (blocking) ─→ storage_capacity ─→ uploaded_snapshots ──┐
                                 ↑                          ↑                    ↑              │
                                 │                          │                    │              │
                          catches corrupt              catches Drive >        catches missing   │
                          / empty SQLite or            90% full (blocking)    files on Drive    │
                          empty / unreadable tgz                              (blocking)        │
                                                                                                ▼
                                                                  prune_drive_backups   prune_local_backups
                                                                          (parallel siblings)

  on job SUCCESS  ──→  ping_healthcheck_on_success (run-status sensor)
                       Absence of ping (within period + grace) is the alert.
```

Any blocking check failure → upload skipped → no success → no ping →
healthchecks alerts. Prune failures fail the run → same cascade.

## Operations

### Run once from CLI

```bash
uv run poe backup
# → dg launch -m orchestrators.defs.pipelines.definitions --job backup_readings
```

### Backfill missing partitions

UI: Assets → group `backup` → select range → **Materialize**.
CLI: `dg launch --job backup_readings --partition <date>`.

### Drive over the threshold

The `drive_capacity_below_threshold` check fails at `>90%`. The materialization
of `storage_capacity` still commits (you keep the timeseries point), but the
upload is gated. Recovery options:

- Free Drive space (manually delete from `<remote>:<DRIVE_BACKUP_ROOT>/`).
- Lower `MAX_DRIVE_BACKUPS` in [`def_config.py`](./def_config.py) and redeploy
  so the next run prunes more aggressively.
- Bump `DRIVE_USAGE_THRESHOLD` (not recommended — you'll soon hit the hard quota).

### Snapshot integrity check failed

The `verify_snapshot_*` blocking check trips when the SQLite file is
suspiciously small, fails `PRAGMA integrity_check`, or has zero tables.
The downstream upload doesn't run — the corrupt snapshot stays local-only.
Common causes: source DB was being written during snapshot (rare, SQLite
backup API handles concurrent reads), disk full on local backup volume.

```bash
sqlite3 backups/<date>/raw_store.db "PRAGMA integrity_check;"
```

### Upload count mismatch

The `all_snapshots_uploaded` blocking check trips when the Drive partition dir
has fewer files than expected after `rclone copy`. The metadata records
`missing` and `extra` lists. Re-run the partition; if it persists,
`rclone copy <local>/<date>/ <remote>/<date>/ -v` manually to inspect.

### Restoring a snapshot

Local (SQLite DBs):
```bash
cp backups/<date>/raw_store.db <BACKUP_SOURCE_DIR>/raw_store.db
```

Local (tgz archives — extract back over the source dir):
```bash
tar -xzf backups/<date>/notes.tgz -C <BACKUP_SOURCE_DIR>
```

From Drive:
```bash
rclone copy gdrive:<DRIVE_BACKUP_ROOT>/<date>/raw_store.db <BACKUP_SOURCE_DIR>/
rclone copy gdrive:<DRIVE_BACKUP_ROOT>/<date>/notes.tgz /tmp/ \
  && tar -xzf /tmp/notes.tgz -C <BACKUP_SOURCE_DIR>
```

## External setup

### rclone OAuth (one-time on laptop, push to server)

The Drive flow needs `rclone` installed and a `gdrive` remote configured on
the machine running `dagster-code`. Drive OAuth needs a browser, so:
configure on your laptop, push the credential to the server.

```bash
brew install rclone     # macOS — Linux: `curl https://rclone.org/install.sh | sudo bash`
rclone config
# n) New remote → name=gdrive → Storage=drive
# client_id, client_secret = blank (uses rclone's shared OAuth client)
# scope = 1 (full Drive) or 3 (drive.file — app-created files only)
# Edit advanced = n; Use auto config = y (browser pops, click Allow)
```

This writes `~/.config/rclone/rclone.conf`. Treat it like an SSH private key.

Verify:
```bash
rclone lsd gdrive:
rclone about gdrive: --json
```

Push to server:
```bash
./scripts/deploy-hcloud.sh push-creds
```

That `rsync`s `~/.config/rclone/rclone.conf` to
`~/knowledge-pipeline/.rclone/rclone.conf` on the server (mode `0600`,
parent dir `0700`). Compose mounts `./.rclone` read-only into `dagster-code`
at `/home/dagster/.config/rclone/`. The token self-refreshes; only re-run
`push-creds` after a re-auth (password change, scope change, fresh
`rclone config`).

### healthchecks.io

1. Sign up at https://healthchecks.io (free tier covers ~20 checks).
2. Create a check named `backup_readings`. Schedule = simple, period 1 day,
   grace 2 hours. (Or cron mode with `0 3 * * *` UTC for exact-time
   enforcement.)
3. Copy the ping URL (`https://hc-ping.com/<uuid>`) into `HEALTHCHECK_PING_URL`
   in the server's `.env`.
4. In the check's *Integrations* tab, attach alert channels (email, Slack,
   Discord, ntfy, Telegram, Pushover, …). healthchecks fans out — no
   per-channel code in this repo.

If no ping arrives within `period + grace` (~26h default), healthchecks fires
"DOWN" alerts. This catches asset failures, broken cron, dead daemon, and
code-location import errors uniformly.
