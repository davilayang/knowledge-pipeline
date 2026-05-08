# Top-level Dagster Definitions — entrypoint for `dagster dev`.
# Merges workbench (index strategies + eval) and pipelines (backup, wiki)
# into a single code location so `orchestrators.definitions` is the single
# module reference for all poe tasks and Dagster config.

import dagster as dg

from orchestrators.defs import shared
from orchestrators.defs.pipelines import backup_readings, synthesize_wiki, upstream_sources
from orchestrators.defs.workbench import (
    evaluate,
    idx_markdown_bge,
    idx_markdown_minilm,
    idx_recursive_minilm,
    idx_semantic_minilm,
)

defs = dg.Definitions.merge(
    shared.defs,
    # workbench (manually triggered)
    idx_markdown_minilm.defs,
    idx_markdown_bge.defs,
    idx_recursive_minilm.defs,
    idx_semantic_minilm.defs,
    evaluate.defs,
    # pipelines (scheduled)
    upstream_sources.defs,
    backup_readings.defs,
    synthesize_wiki.defs,
)
