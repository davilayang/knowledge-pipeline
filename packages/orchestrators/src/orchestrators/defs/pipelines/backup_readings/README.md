# `backup_readings` pipeline

Daily-partitioned snapshot of the newsletter-assistant SQLite databases, with
optional Google Drive offload via rclone and a healthchecks.io ping that turns
silence (cron didn't fire, daemon died) into a loud alert.

## DAG (per partition)

```
snapshot_raw_store ─┐
                    ├─→ verify_* (blocking) ─→ check_drive_capacity ─→ upload_snapshots_to_drive ─┐
snapshot_sessions  ─┘                                                                              │
                                                                                                   │
                                                            ┌──────────────────────────────────────┤
                                                            ▼                                      ▼
                                                  prune_drive_backups                  prune_local_backups
                                                                            (parallel siblings of upload)


  on job SUCCESS  ──→  ping_healthcheck_on_success (run-status sensor; not part of the asset graph)
                       POST to healthchecks.io. Absence of ping (within period + grace) is the alert.
```

The healthcheck ping is **not an asset** — it's a run-status sensor that fires
once on successful job completion. The ping itself has no per-partition history
worth keeping (healthchecks.io maintains its own ping log), so modeling it as a
sensor avoids stretching "asset" to mean "any side effect."

Prune failures fail the run → no success ping → healthchecks alerts. Prune
failures show as red in the Dagster UI in either case.

Each daily partition produces:

| Asset | What it does |
|---|---|
| `snapshots/raw_store` | SQLite `.backup()` of `raw_store.db` → `BACKUP_DIR/<date>/raw_store.db` |
| `snapshots/sessions` | Same, for `sessions.db` |
| `verify_snapshot_*` | **Blocking** asset checks: file size, `PRAGMA integrity_check`, table count |
| `drive/capacity` | `rclone about` preflight; raises Failure at `>90%` Drive usage |
| `drive/uploaded` | `rclone copy` of the partition dir to `<remote>:knowledge-pipeline-backups/<date>/` |
| `drive/pruned` | Keep newest `MAX_DRIVE_BACKUPS=90` partition dirs on Drive |
| `local/pruned` | Keep newest `MAX_LOCAL_BACKUPS=14` partition dirs on disk |

Plus one **sensor** (not an asset):

| Sensor | What it does |
|---|---|
| `ping_healthcheck_on_success` | On successful `backup_readings` run, POSTs to `HEALTHCHECK_PING_URL`. |

**Schedule:** `run_daily_backup` fires `0 3 * * *` UTC for the previous day's
partition, `run_key=<date>` (Dagster dedupes accidental double-fires).

## Configuration

| Env var | Default | Effect when unset |
|---|---|---|
| `BACKUP_SOURCE_DIR` | `~/newsletter-assistant/data` | Uses default — set on laptops to `~/GitHub/newsletter-assistant/data` |
| `BACKUP_DIR` | `<repo>/backups` | Uses default |
| `DRIVE_REMOTE` | _(empty)_ | `drive/*` assets short-circuit; run still succeeds |
| `HEALTHCHECK_PING_URL` | _(empty)_ | `healthcheck/pinged` short-circuits |

Tunables in [`constants.py`](./constants.py): `MAX_LOCAL_BACKUPS`,
`MAX_DRIVE_BACKUPS`, `DRIVE_USAGE_THRESHOLD`, `DRIVE_ROOT`.

## rclone setup (laptop config → server)

The Drive flow needs `rclone` installed and a configured `gdrive` remote on the
machine running the `dagster-code` container. Because Drive OAuth needs a
browser, the simplest pattern is **configure on your laptop, push the credential
to the server**.

### 1. One-time on your laptop

```bash
brew install rclone     # macOS — Linux: `curl https://rclone.org/install.sh | sudo bash`
rclone config
# choose:
#   n) New remote
#   name> gdrive
#   Storage> drive
#   client_id>     (blank — uses rclone's shared OAuth client)
#   client_secret> (blank)
#   scope> 1                          (full Drive)  or  3 (drive.file — app-created files only)
#   service_account_file>  (blank)
#   Edit advanced config> n
#   Use auto config> y                (browser pops, you click Allow)
```

This writes `~/.config/rclone/rclone.conf` containing the OAuth refresh token.
Treat it like an SSH private key.

Verify the remote works:

```bash
rclone lsd gdrive:
rclone about gdrive: --json
```

### 2. Push the credential to the server

```bash
./scripts/deploy-hcloud.sh push-creds
```

That `rsync`s `~/.config/rclone/rclone.conf` from your laptop to
`~/knowledge-pipeline/.rclone/rclone.conf` on the server (mode `0600`,
parent dir `0700`). `docker-compose.yml` mounts `./.rclone` read-only into
`dagster-code` at `/root/.config/rclone`, which is rclone's default search path.

The token self-refreshes on every invocation, so you only re-run `push-creds`
if you re-auth (Google password change, scope change, or running `rclone
config` again).

### 3. Set the env vars on the server

In `~/knowledge-pipeline/.env` on the server:

```bash
DRIVE_REMOTE=gdrive
HEALTHCHECK_PING_URL=https://hc-ping.com/<your-uuid>
# BACKUP_SOURCE_DIR is left at the default (~/newsletter-assistant/data)
```

Then redeploy: `./scripts/deploy-hcloud.sh deploy --no-build`. The container
restart picks up the new env, and the Drive + healthcheck assets stop
short-circuiting.

## healthchecks.io setup

1. Sign up at https://healthchecks.io (free tier covers ~20 checks).
2. Create a new check:
   - **Name:** `backup_readings`
   - **Schedule:** Simple, period **1 day**, grace **2 hours**.
   - (Or "cron" mode with `0 3 * * *` UTC if you want exact-time enforcement.)
3. Copy the ping URL (`https://hc-ping.com/<uuid>`) into `HEALTHCHECK_PING_URL`.
4. In the check's *Integrations* tab, attach your alert channel(s): email,
   Slack, Discord, ntfy, Telegram, Pushover, etc. healthchecks fans out the
   notification — **no per-channel code in this repo**.

When a run succeeds, the terminal `ping_healthcheck` asset POSTs to that URL.
If no ping arrives within `period + grace` (~26h by default), healthchecks
sends "DOWN" alerts via your configured channels. This catches asset failures,
broken cron, dead daemon, and code-location import errors uniformly.

## Operations

### Run once from CLI

```bash
uv run poe backup    # → dg launch -m orchestrators.defs.pipelines.definitions --job backup_readings
```

### Backfill missing partitions

In the Dagster UI: Assets → group `backup` → select range → **Materialize**.
Or via CLI: `dg launch --job backup_readings --partition <date>`.

### Drive over the threshold

`check_drive_capacity` raises Dagster Failure at `>90%`. Either:

- Free Drive space (manually delete from `<remote>:knowledge-pipeline-backups/`)
- Lower `MAX_DRIVE_BACKUPS` in [`constants.py`](./constants.py) and re-deploy
- Bump `DRIVE_USAGE_THRESHOLD` (not recommended — you'll soon hit the hard quota)

### Restoring a snapshot

Local:
```bash
cp ~/knowledge-pipeline/backups/<date>/raw_store.db ~/newsletter-assistant/data/raw_store.db
```

From Drive:
```bash
rclone copy gdrive:knowledge-pipeline-backups/<date>/raw_store.db ~/newsletter-assistant/data/
```
