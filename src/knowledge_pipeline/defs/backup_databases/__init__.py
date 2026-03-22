import dagster as dg

from .ops import backup_graph
from .resources import BackupResource

backup_databases_job = backup_graph.to_job(
    name="backup_databases_job",
    description="Back up SQLite databases from newsletter-assistant with retention cleanup",
)

defs = dg.Definitions(
    jobs=[backup_databases_job],
    resources={
        "backup": BackupResource(),
    },
)
