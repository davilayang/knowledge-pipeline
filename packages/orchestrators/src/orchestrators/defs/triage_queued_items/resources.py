"""Resources for the triage_queued_items pipeline.

Both the Notion Queue access and the local queue.db wrapper live in
`orchestrators.defs.shared.queue_resources` since they are shared with
the extract pipeline. This module is now responsible only for binding
those shared classes to the pipeline's resource keys.
"""

import dagster as dg

from orchestrators.defs.shared.queue_resources import (
    NotionQueueResource,
    QueueStoreResource,
)


def build_resources() -> dict[str, dg.ConfigurableResource]:
    return {
        "triage_notion": NotionQueueResource(
            integration_token=dg.EnvVar("NOTION_INTEGRATION_TOKEN"),
            queue_db_id=dg.EnvVar("NOTION_QUEUE_DB_ID"),
            queue_data_source_id=dg.EnvVar("NOTION_QUEUE_DATA_SOURCE_ID"),
        ),
        "triage_store": QueueStoreResource(),
    }
