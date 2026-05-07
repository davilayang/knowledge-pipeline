# Dagster Pipeline Style Guide

Conventions for building Dagster pipelines in this repo. Synthesised from
`packages/orchestrators/src/orchestrators/defs/pipelines/backup_readings/`
(the canonical reference), cross-checked against
[dagster-open-platform](https://github.com/dagster-io/dagster-open-platform)
(DOP). Use this when revamping existing DAGs, scaffolding new ones, or
deciding which Dagster API to reach for.

`backup_readings/` is the live exemplar — when in doubt, look there first.

## Pipeline scaffold

One pipeline = one folder under
`packages/orchestrators/src/orchestrators/defs/pipelines/<name>/`. Eight files:

```
<pipeline>/
  __init__.py        # aggregates into a single dg.Definitions and re-exports
  README.md          # DAG diagram, env vars, runbook, restore procedure
  assets.py          # @dg.asset definitions + private _helper functions
  checks.py          # @dg.asset_check standalone checks (omit if none)
  def_config.py      # pure constants — no env reads, no functions
  resources.py       # ConfigurableResource subclasses + build_resources()
  schedules.py       # asset job + schedule
  sensors.py         # run-status / failure sensors (omit if none)
```

Drop `checks.py` or `sensors.py` only when the pipeline genuinely has none.
Don't manufacture content to fill a slot.

The pipeline-level `defs` is the only public symbol from `__init__.py`. The
parent `pipelines/definitions.py` merges all pipeline `defs` via
`dg.Definitions.merge(...)`. We do **not** use `dg.components.load_defs()` —
the explicit merge is more readable at our scale.

## Assets

### Decorator argument checklist

Every asset gets the full set; none of these are optional:

```python
@dg.asset(
    key=["snapshots", "raw_store"],                # nested list, not dot-string
    group_name="backup",                           # one group per pipeline
    compute_kind="sqlite",                         # icon-supported name (see below)
    code_version=BACKUP_READINGS_DAG_VERSION,      # from orchestrators.config
    partitions_def=daily_partition_def,            # same instance across pipeline
    op_tags={"dagster/concurrency_key": PIPELINE_TAG},  # see "Concurrency"
    description="Consistent SQLite snapshot of raw_store.db for the partition.",
)
def snapshot_raw_store(context, backup): ...
```

### `compute_kind` — use icon-supported names

Dagster renders a badge for each asset based on `compute_kind`. Only certain
strings have built-in icons (see
[supported icons](https://docs.dagster.io/guides/build/assets/metadata-and-tags/kind-tags#supported-icons)).
Pick names that describe the **system being operated on**, not the **tool used
to operate it**:

| Use | Not |
|---|---|
| `googledrive` | `rclone`, `google_drive` |
| `sqlite` | `python` |
| `file` | `filesystem` |
| `snowflake` | `boto3`, `psycopg` |

If a tool is swapped tomorrow (rclone → `gcloud storage cp`) the kind shouldn't
change. The integration is durable; the binary isn't.

### Concurrency

Every asset in a pipeline shares one `op_tags={"dagster/concurrency_key": ...}`
key, defined as `PIPELINE_TAG` in `def_config.py`. This:

- Throttles parallelism within the pipeline (avoids SQLite lock contention,
  rclone rate limits, etc.)
- Gives operators a single global lever — they can throttle the pipeline via
  instance config without touching code.

### Dependencies

Use `deps=[dg.AssetDep([...])]` with explicit asset keys. Don't rely on
parameter injection for cross-asset dependencies — `deps=` makes the graph
readable from the decorator without reading the function body.

### Code versioning

Each pipeline owns a `<NAME>_DAG_VERSION = "1"` constant in
`packages/orchestrators/src/orchestrators/config.py`, applied as `code_version=`
on every asset in that pipeline. Bump manually when DAG logic changes
(asset signatures, dep changes, semantics moving between asset and check).
**Don't tie this to package version** — the version-bump skill rolls package
versions on every release, which would otherwise mark every asset stale.

Naming: `BACKUP_<DOMAIN>_DAG_VERSION` for backup pipelines (alphabetical
grouping in `config.py`); `<DOMAIN>_DAG_VERSION` for everything else.

### Return shape — `MaterializeResult` with structured metadata

```python
return dg.MaterializeResult(
    metadata={
        "size_mb": dg.MetadataValue.float(size / (1024 * 1024)),
        "sha256": dg.MetadataValue.text(digest),
        "source_path": dg.MetadataValue.path(str(source)),
        "summary": dg.MetadataValue.md("**X** — kept 7 / deleted 0"),
    }
)
```

Use typed `MetadataValue` (`.float`, `.int`, `.text`, `.path`, `.json`,
`.md`). The markdown summary is what operators read in the UI — treat it as a
runbook surface.

### Don't write redundant `status: "ok"` metadata

A successful materialization already implies "ok" by definition. The field
adds a row to the UI without conveying information. Also drop wrapper status
text like `"skipped"` or `"no_root"` — use a `summary` markdown field
("_no backup root_") if a human-readable cue is useful, or drop it entirely.

### Fail loud on broken state

```python
if not source.exists():
    raise dg.Failure(
        description=f"Source DB missing: {source}",
        metadata={"source_path": dg.MetadataValue.path(str(source))},
    )
```

Don't warn-and-skip-with-empty-result for broken state. A bind mount missing,
a source app moved files, an env var unset — these aren't "skip this
partition," they're "the pipeline's contract is violated." `dg.Failure` makes
that visible; `context.log.warning` + empty `MaterializeResult` makes a real
ops failure look green.

### Don't mix measurement and policy in one asset

If an asset both observes something (records `used_pct`, etc.) and enforces a
threshold (raises `dg.Failure` when over), it's two responsibilities. The
threshold-violation case loses the observation — you scroll back through
history and see gaps where the data would be most informative. Split:

- Asset = measurement. Always materializes if the work succeeds. Records the
  observed values.
- Asset check = policy. Evaluates the invariant. `blocking=True` to gate
  downstream when violated.

See the next section for how to wire that.

## Asset checks — three patterns

### Pattern 1: Standalone `@dg.asset_check` (DOP-style)

Use when the check validates an **external store independent of the asset's
run** — e.g. "is this Snowflake table free of duplicates?" The check can run
ad-hoc from the UI without re-materializing.

```python
@dg.asset_check(
    asset=dg.AssetKey(["snapshots", "raw_store"]),
    name="verify_snapshot_raw_store",
    blocking=True,
    description="raw_store.db opens, integrity_check passes, has tables.",
)
def verify_snapshot_raw_store(
    context: dg.AssetCheckExecutionContext, backup: BackupResource
) -> dg.AssetCheckResult:
    # Open the file from disk, validate, return AssetCheckResult.
    ...
```

Defined in `checks.py`. Exported in `all_checks` and wired into `Definitions`
via `asset_checks=`.

### Pattern 2: Co-emitted via `check_specs=` (when validating the run's output)

Use when the check is validating the **specific value the asset just
observed** — re-querying the underlying system would be wasteful (duplicate
external call) and could give a stale-by-1-second answer. The asset declares
the check spec in its decorator and yields both the materialization and the
check result.

```python
@dg.asset(
    key=["google_drive", "storage_capacity"],
    ...
    check_specs=[
        dg.AssetCheckSpec(
            name="drive_capacity_below_threshold",
            asset=dg.AssetKey(["google_drive", "storage_capacity"]),
            blocking=True,
            description=f"Fail when used_pct > {DRIVE_USAGE_THRESHOLD:.0%}.",
        )
    ],
    description="Daily Drive usage observation; co-emits the threshold check.",
)
def storage_capacity(context, rclone):
    used_pct = ...  # observe

    yield dg.MaterializeResult(metadata={"used_pct": ...})
    yield dg.AssetCheckResult(
        check_name="drive_capacity_below_threshold",
        passed=used_pct <= DRIVE_USAGE_THRESHOLD,
        severity=dg.AssetCheckSeverity.ERROR,
        metadata={"used_pct": ..., "threshold": ...},
    )
```

Single source of truth, atomic with the materialization. Trade-off: the check
can't run independently — it's pinned to the materialization. For "validate
the run's outcome" that's correct; for "is the external state currently OK?"
prefer pattern 1.

### Pattern 3: No check at all

Don't add checks for **deterministic local logic** whose only failure modes
are bugs in the code itself. Example: prune assets that compute
`to_delete = dirs[:-MAX]` and assert `kept_count <= MAX` afterward — that's a
unit test, not a runtime invariant worth checking on every partition.

Ask: "what real failure mode does this check catch?" If the answer is "Python
slicing not working" or "I might have a bug here," skip the check. Add a unit
test instead.

### `blocking` choice

- `blocking=True` — failed check stops downstream materialization in the same
  run. Use for hard gates (snapshot integrity, capacity threshold, upload
  completion).
- `blocking=False` — alert-only. Use when the check is informational and
  shouldn't gate the rest of the run.

## Resources

### `ConfigurableResource` subclasses

```python
class RcloneResource(dg.ConfigurableResource):
    """rclone remote for Drive upload + retention."""

    remote_name: str
    drive_root: str

    def remote_path(self, *parts: str) -> str:
        joined = "/".join(p.strip("/") for p in parts if p)
        return f"{self.remote_name}:{joined}"
```

Inherit from `dg.ConfigurableResource`. Plain Pydantic-flavoured fields.
Methods for derived values; properties for stateless lookups.

### Required env vars use `dg.EnvVar(...)`, not `os.getenv` with defaults

For per-deployment knobs (credentials, paths, integration roots), the
convention is **required, no default**. The server's `.env` provides the
value; an unset env var fails fast at run init.

```python
def build_resources() -> dict[str, dg.ConfigurableResource]:
    return {
        "backup": BackupResource(),
        "rclone": RcloneResource(
            remote_name=dg.EnvVar("DRIVE_REMOTE"),
            drive_root=dg.EnvVar("DRIVE_BACKUP_ROOT"),
        ),
        "healthcheck": HealthcheckResource(ping_url=dg.EnvVar("HEALTHCHECK_PING_URL")),
    }
```

Why no defaults: a code-level default that quietly applies in prod when
someone forgets to set the env is worse than failing fast. Symmetric required
envs across environments mean every deploy is explicit about its own config.

`dg.EnvVar` is **late-binding** — definitions still load even if the env is
unset, so the gRPC code-server starts and laptop dev that doesn't need the
resource still works. Only a run that actually requires the resource fails
fast. (See PR #35 / #37 for the pattern in action.)

**Exception:** `os.getenv(..., default)` is appropriate for **truly
code-stable** defaults — e.g. a relative path like `BACKUP_DIR=./backups`
that makes sense in every environment. The test is "would this same value
ever be wrong in any deploy?" If yes, use `dg.EnvVar` and require it.

### `is_configured` short-circuits — don't

Avoid resource-level `is_configured` properties that gate asset bodies into
"skip" branches. Two reasons:

1. The "skip" path silently masks misconfiguration — same anti-pattern as
   warn-and-skip on missing source files.
2. It bleeds dev convenience into the pipeline contract. The pipeline has
   one production behaviour; "I'm too lazy to configure rclone on my laptop"
   isn't a configuration of the pipeline, it's a different selection over the
   pipeline. Run a subset job for laptop dev instead.

## Schedules and jobs

One asset job + one schedule per pipeline. Explicit asset selection (no
wildcards):

```python
backup_readings_job = dg.define_asset_job(
    name="backup_readings",
    selection=dg.AssetSelection.assets(*[a.key for a in all_assets]),
)

@dg.schedule(cron_schedule="0 3 * * *", job=backup_readings_job)
def run_daily_backup(context):
    yield dg.RunRequest(
        partition_key=context.scheduled_execution_time.date().isoformat(),
        run_key=...,  # Dagster dedupes accidental double-fires
    )
```

`partitions_def` on `define_asset_job` is deprecated — partitioning is
inferred from the selected assets. Don't set it.

## Sensors

Run-status sensors for ephemeral side-effects (healthcheck pings, Slack
notifications). The healthcheck ping is a sensor, not an asset, because the
ping itself has no per-partition history worth keeping — healthchecks.io
maintains its own ping log.

```python
@dg.run_status_sensor(
    run_status=dg.DagsterRunStatus.SUCCESS,
    monitored_jobs=[backup_readings_job],
    minimum_interval_seconds=SENSOR_MIN_INTERVAL_S,
)
def ping_healthcheck_on_success(context, healthcheck):
    ...
```

For a daily job, `minimum_interval_seconds=300` (5 min) is fine — the daemon
default of 30s is wasteful when the sensor only fires on a new SUCCESS event
once a day.

This is a deliberate divergence from DOP, which models sensors as
data-discovery only. Document the rationale inline if you go this route.

## `def_config.py` — pure constants only

```python
PIPELINE_TAG = "newsletter-backup"
MAX_LOCAL_BACKUPS = 14
DRIVE_USAGE_THRESHOLD = 0.90
SCHEDULE_CRON = "0 3 * * *"
daily_partition_def = dg.DailyPartitionsDefinition(start_date="2026-01-01")
```

No env reads, no functions, no I/O. Tunables that ship with the code (and are
the same across deployments). Per-deployment values live on resources via
`dg.EnvVar`; path-level config that orchestrators read (e.g. `BACKUP_DIR`)
goes in `orchestrators/config.py`.

Why this split: tunables ship with the code (versioned), env vars are
per-deployment. Mixing them blurs which knob lives where.

## `__init__.py` — assemble, no logic

```python
import dagster as dg

from .assets import all_assets
from .checks import all_checks
from .resources import build_resources
from .schedules import backup_readings_job, run_daily_backup
from .sensors import all_sensors

defs = dg.Definitions(
    assets=all_assets,
    asset_checks=all_checks,
    jobs=[backup_readings_job],
    schedules=[run_daily_backup],
    sensors=all_sensors,
    resources=build_resources(),
)
```

Only public symbol is `defs`. Don't expose helpers from here.

## README — every pipeline has one

Sections:

1. **DAG diagram** (mermaid or ASCII art).
2. **Partitions** — scheme, start date, backfill semantics.
3. **Env vars** — required vs. optional, what each controls.
4. **Operational runbook** — backfill, restore, alert wiring.
5. **Layer-2 testing** — sandbox materialisation pattern (see existing READMEs).

Future operators (including future-you) read this before opening any `.py`
file. Treat it as code, not docs — keep it current.

## Comments — default to none

Per the global CLAUDE.md rule, **default to no comments**. Add one only when
the WHY is non-obvious — a hidden constraint, a subtle invariant, a
workaround for a specific bug. If removing the comment wouldn't confuse a
future reader, don't write it.

Specifically, **don't** add comments that:

- Explain *why* a design choice was made when the code structure already
  encodes it (e.g. "we use `dg.EnvVar()` for late binding so..." — the call
  itself + Dagster docs are enough).
- Re-state what the asset graph already shows (e.g. "this dep is intentional
  because..." — the dep declaration is the constraint).
- Document Dagster usage that the API surface communicates (e.g. "asset
  checks gate downstream when blocking=True").

The reader will figure it out. Save comment budget for genuine
non-obviousness.

## Patterns lifted from DOP

- File split (`assets.py` / `resources.py` / `schedules.py` / `sensors.py`).
- Concurrency keys via `op_tags`.
- Explicit asset lists for job selection.
- Metadata-rich `MaterializeResult` with typed `MetadataValue`.
- Asset checks that re-query external stores (Snowflake-style) when the
  invariant is "is the external state currently OK?"
- `compute_kind` describes the system, not the tool.

## Patterns we deliberately don't copy from DOP

- **`@definitions` decorator + `load_defs()`** — Beta API; manual
  `Definitions.merge(...)` is clearer with our pipeline count. Revisit when
  >10 pipelines or when we want org-wide post-discovery policies.
- **`dg.EnvVar()` for absolutely everything** — we keep `os.getenv(..., default)`
  for code-stable defaults (e.g. `BACKUP_DIR=./backups`).
- **Inline asset checks** — keep `checks.py` separate. Easier to audit which
  invariants are enforced.

## Anti-patterns to remove on revamp

When refactoring an existing DAG, look for and fix:

- **Mixed measurement + policy** in one asset (raises `dg.Failure` on a
  threshold). Split into measurement asset + co-emitted check.
- **`is_configured` short-circuits** on optional resources. Make required;
  laptop dev runs a subset.
- **Warn-and-skip on missing source/state**. Replace with `dg.Failure`.
- **Redundant `status: "ok"` / `"skipped"` / `"no_root"` metadata**. Drop;
  let materialization success/failure carry the signal.
- **`compute_kind` set to a tool name** (`rclone`, `python`) instead of the
  system (`googledrive`, `snowflake`).
- **Hardcoded paths for per-deployment values**. Move to `dg.EnvVar` field on
  the relevant resource.
- **Comments narrating design decisions** the code already encodes.

## Checklist for a new DAG

- [ ] Folder under `packages/orchestrators/src/orchestrators/defs/pipelines/<name>/`.
- [ ] `<NAME>_DAG_VERSION = "1"` constant added to `orchestrators/config.py`.
- [ ] Eight files (drop `checks.py` / `sensors.py` only if genuinely empty).
- [ ] All assets share one `group_name`, one `partitions_def`, one
      `concurrency_key`, the same `code_version` constant.
- [ ] `compute_kind` values use icon-supported names that describe the system.
- [ ] All per-deployment env vars use `dg.EnvVar(...)` with no code-level
      default; documented in `.env.example`.
- [ ] Broken-state guards use `dg.Failure`, not warn-and-skip.
- [ ] Measurement vs. policy split via asset checks (standalone or co-emitted
      depending on whether the check is independent or run-bound).
- [ ] No redundant `status` metadata fields.
- [ ] README has DAG diagram, env var list, restore runbook.
- [ ] Pipeline `defs` merged into `pipelines/definitions.py` and a sandbox
      test under `tests/<pipeline_name>/`.
- [ ] Run `uv run poe check` clean (fmt, lint, all tests).
