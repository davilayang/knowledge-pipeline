"""Partition definitions shared across pipelines.

Imported by triage_knowledge_queue and fetch_extract_queue so a single
notion_page_id partition exists across the full queue lifecycle. The
partition-def name (`queue_items`) is the registration key in Dagster's
instance metadata; renaming it orphans the existing partition registry.
"""

import dagster as dg

queue_items_partition_def = dg.DynamicPartitionsDefinition(name="queue_items")
