# Run-status sensor for the terminal healthchecks.io ping.
#
# The ping is a side effect of "the backup_readings run succeeded" — it has no
# per-partition metadata worth keeping in Dagster, so it lives here as a sensor
# rather than as an asset. healthchecks.io maintains its own ping history and
# alerts on absence.

import logging
import urllib.request

import dagster as dg

from .resources import HealthcheckResource
from .schedules import backup_readings_job

logger = logging.getLogger(__name__)


@dg.run_status_sensor(
    run_status=dg.DagsterRunStatus.SUCCESS,
    monitored_jobs=[backup_readings_job],
    description=(
        "On successful backup_readings run, POST to healthchecks.io. "
        "Absence of this ping (within healthchecks period + grace) is the failure alert."
    ),
)
def ping_healthcheck_on_success(
    context: dg.RunStatusSensorContext, healthcheck: HealthcheckResource
) -> None:
    if not healthcheck.is_configured:
        context.log.info("HEALTHCHECK_PING_URL unset; skipping ping.")
        return

    partition = context.dagster_run.tags.get("dagster/partition", "unknown")
    body = f"backup_readings ok for partition={partition}".encode()
    req = urllib.request.Request(healthcheck.ping_url, data=body, method="POST")
    with urllib.request.urlopen(req, timeout=10) as resp:
        resp.read()

    context.log.info("Pinged healthchecks for partition=%s", partition)


all_sensors = [ping_healthcheck_on_success]
