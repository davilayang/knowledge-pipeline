"""Schedule wiring for synthesize_wiki.

The schedule's only job is to fire partition D-1 on day D — same key
backup_readings materialised at 03:00 UTC. A timezone mistake or
off-by-one would silently couple wiki(D) to snapshot(D), which doesn't
exist on day D until the next morning's backup tick.
"""

from datetime import UTC, datetime
from unittest.mock import MagicMock

import dagster as dg
from orchestrators.defs.pipelines.synthesize_wiki.schedules import run_daily_synthesize_wiki


def test_schedule_emits_d_minus_1_partition_key():
    ctx = MagicMock(spec=dg.ScheduleEvaluationContext)
    ctx.scheduled_execution_time = datetime(2026, 5, 8, 6, 0, tzinfo=UTC)

    result = run_daily_synthesize_wiki(ctx)

    assert isinstance(result, dg.RunRequest)
    assert result.partition_key == "2026-05-07"
    assert result.run_key == "2026-05-07"
