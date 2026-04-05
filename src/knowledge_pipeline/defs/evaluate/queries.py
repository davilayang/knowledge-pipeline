# Curated evaluation queries with ground-truth content IDs.
#
# Ground truth is at the content_id level (not chunk_id) so it survives
# re-chunking across strategies.  Populate by running queries against the
# baseline collection, eyeballing the results, and recording which
# content_ids are genuinely relevant.
#
# Curation workflow:
#   1. Run a query manually against the baseline collection
#   2. Inspect returned chunks and their content_ids
#   3. Decide which content_ids are actually relevant answers
#   4. Add an EvalQuery entry below

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class EvalQuery:
    query: str
    expected_content_ids: list[str] = field(default_factory=list)
    category: str = "general"  # factual, topical, broad, specific


# --- Populate after inspecting raw_store.db contents ---
# Example:
#   EvalQuery(
#       query="How does RLHF work?",
#       expected_content_ids=["content_abc123", "content_def456"],
#       category="factual",
#   ),
EVAL_QUERIES: list[EvalQuery] = []
