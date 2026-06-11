"""Partition definitions for queue-driven pipelines.

Each pipeline owns its own DynamicPartitionsDefinition keyed on
notion_page_id, so a manual Status flip in Notion or a partition-registry
wipe in one pipeline can't strand a sensor tick in another. The partition
key namespace (notion_page_id) is shared by convention, not by definition.

The def *name* (`queue_items`, `extract_queue_items`) is the registration
key in Dagster's instance metadata; renaming it orphans the existing
partition registry.
"""

import dagster as dg

queue_items_partition_def = dg.DynamicPartitionsDefinition(name="queue_items")
extract_queue_items_partition_def = dg.DynamicPartitionsDefinition(name="extract_queue_items")
