"""Resources for the triage_knowledge_queue pipeline.

Both the Notion Queue access and the local queue.db wrapper live in
`orchestrators.defs.shared.queue_resources` since they are shared with
the extract pipeline. This module is responsible for binding those
shared classes + the pipeline-local `ContentShapeClassifier` to the
pipeline's resource keys.
"""

import os

import dagster as dg

from orchestrators.defs.shared.queue_resources import (
    NotionQueueResource,
    QueueStoreResource,
)

from .content_shape_llm import ContentShapeClassifier


def build_resources() -> dict[str, dg.ConfigurableResource]:
    return {
        "triage_notion": NotionQueueResource(
            integration_token=dg.EnvVar("NOTION_INTEGRATION_TOKEN"),
            queue_db_id=dg.EnvVar("NOTION_QUEUE_DB_ID"),
            queue_data_source_id=dg.EnvVar("NOTION_QUEUE_DATA_SOURCE_ID"),
        ),
        "triage_store": QueueStoreResource(),
        # Optional keys via os.environ.get (not dg.EnvVar) — both unset
        # means triage falls through to content_shape="unknown", which is
        # the correct behaviour on a deploy that hasn't enabled the LLM.
        "content_shape_classifier": ContentShapeClassifier(
            groq_api_key=os.environ.get("GROQ_API_KEY"),
            openai_api_key=os.environ.get("OPENAI_API_KEY"),
        ),
    }
