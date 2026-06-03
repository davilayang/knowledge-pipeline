"""One-shot prod migration: queue.db legacy single-shot columns → three-call shape.

Runs against `data/queue.db` on the Hetzner host:

    ssh hcloud
    cd /home/deploy/knowledge-pipeline
    uv run python scripts/migrate_extraction_to_calls_table.py

Idempotent — safe to re-run. The real work happens inside
`domains.queue_store.sources.create_schema()`:

1. ADD new `queue_items` columns (`extractor_label`, `extractor_sha256`,
   `tokens_in_total`, `tokens_out_total`, `langfuse_trace_id`).
2. CREATE the `extraction_calls` table + indexes.
3. DROP the legacy `idx_queue_items_prompt_label` index.
4. DROP the legacy single-shot columns (`extraction_payload`,
   `extraction_prompt_label`, `prompt_sha256`, `tokens_in`, `tokens_out`).

The legacy columns are NOT preserved. The ~7 already-extracted rows lose
their old single-shot blob in this step; re-trigger Dagster
`extract_complex_contents/extracted` on those partitions after the
migration so they get the new three-call shape. ~7 rows × 3 calls ×
~5K tokens, approximately $0.20-0.50 in OpenAI spend.

Why no preservation: with the three-call shape live, the old blob is
unreadable in NA's path (the consumer reads `extraction_calls.output`
now, not the legacy blob). Keeping the columns adds carry cost without
a consumer; the seven rows re-extract cheaply.
"""

import sys
from pathlib import Path

# Default to the kp data directory relative to repo root.
DEFAULT_QUEUE_DB = Path(__file__).resolve().parents[1] / "data" / "queue.db"


def main(db_path: Path = DEFAULT_QUEUE_DB) -> int:
    if not db_path.exists():
        print(f"queue.db not found at {db_path}", file=sys.stderr)
        return 1

    # Import inside main so the script doesn't import dagster — orchestrators
    # is heavy + depends on env that may not exist on the migration host.
    from domains.queue_store.sources import create_schema

    print(f"Running create_schema on {db_path} (idempotent)...")
    create_schema(db_path=db_path)

    print("Migration complete.")
    print(
        "\nNext steps:\n"
        "  1. Re-trigger Dagster `extract_complex_contents/extracted` on every\n"
        "     queue_items row that previously had an extraction (clear\n"
        "     `extracted_at` via the Dagster UI or re-materialise per\n"
        "     partition). Each row triggers three new LLM calls.\n"
        "  2. Verify via:\n"
        "     SELECT q.notion_page_id, q.extractor_label,\n"
        "            json_extract(c_t.output, '$.extracted_title') AS title,\n"
        "            json_array_length(json_extract(c_f.output, '$.questions')) AS n_chips\n"
        "       FROM queue_items q\n"
        "       LEFT JOIN extraction_calls c_t\n"
        "         ON c_t.notion_page_id = q.notion_page_id AND c_t.call_kind = 'topic_card'\n"
        "       LEFT JOIN extraction_calls c_f\n"
        "         ON c_f.notion_page_id = q.notion_page_id AND c_f.call_kind = 'followups'\n"
        "      WHERE q.extracted_at IS NOT NULL;"
    )
    return 0


if __name__ == "__main__":
    db_arg = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_QUEUE_DB
    sys.exit(main(db_arg))
