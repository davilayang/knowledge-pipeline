"""Schedule wiring for populate_vector_store."""

from datetime import UTC, datetime
from unittest.mock import MagicMock

import dagster as dg
from orchestrators.defs.populate_vector_store.def_config import (
    vector_store_partition_def,
)
from orchestrators.defs.populate_vector_store.schedules import (
    run_populate_vector_store,
)


def test_schedule_emits_current_half_hour_partition_key():
    ctx = MagicMock(spec=dg.ScheduleEvaluationContext)
    ctx.scheduled_execution_time = datetime(2026, 5, 11, 14, 30, tzinfo=UTC)

    result = run_populate_vector_store(ctx)

    assert isinstance(result, dg.RunRequest)
    assert result.partition_key == "2026-05-11-14:30"
    assert result.run_key == "2026-05-11-14:30"


def test_partition_def_recognises_half_hour_keys():
    for key in ("2026-05-15-15:00", "2026-05-15-15:30", "2026-05-15-16:30"):
        assert vector_store_partition_def.has_partition_key(key), key
