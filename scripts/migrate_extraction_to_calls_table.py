"""One-shot prod migration for the three-call extraction refactor.

Runs against `data/queue.db` on the Hetzner host:

    ssh hcloud
    cd /home/deploy/knowledge-pipeline
    uv run python scripts/migrate_extraction_to_calls_table.py

Idempotent — safe to re-run. The actual schema additions are also
idempotent in `domains.queue_store.sources.create_schema()`; this script's
extra job is seeding `extraction_calls` from existing legacy rows so
NA's read path doesn't see a hole on already-extracted pages.

What it does:

1. Calls `create_schema()` — adds the new columns + creates the
   `extraction_calls` table + indexes if not present.
2. For each `queue_items` row where `extracted_at IS NOT NULL` AND
   `extraction_payload IS NOT NULL` AND there are no `extraction_calls`
   rows yet: inserts ONE seed row with `call_kind='legacy_v5'` carrying
   the JSON blob in `output`. NA's three-call reader skips
   `legacy_v5` rows; legacy single-shot readers still find the blob in
   `queue_items.extraction_payload` (column retained for one release).
3. Updates `queue_items.extractor_label='legacy_v5'` and
   `extractor_sha256=<prompt_sha256 as best-available proxy>` on the
   same rows, plus `tokens_in_total` / `tokens_out_total` from the
   legacy per-call columns.
4. Does NOT drop legacy columns. The plan's §Rollback recommendation
   retains them for one release cycle — drop in a follow-up migration
   after three-call quality is confirmed in prod.

After this script:

- Trigger Dagster `extract_complex_contents/extracted` on the 7-ish
  legacy rows (clear extracted_at via UI or re-materialise per
  partition) so they get the new shape. Re-extraction cost:
  ~7 rows x 3 calls x ~5K tokens, approximately $0.20-0.50.
"""

import sqlite3
import sys
from pathlib import Path

# Resolve queue.db path the same way kp does in prod. Production layout:
#   /home/deploy/knowledge-pipeline/data/queue.db
# Local layout: ./data/queue.db relative to repo root.
DEFAULT_QUEUE_DB = Path(__file__).resolve().parents[1] / "data" / "queue.db"


def main(db_path: Path = DEFAULT_QUEUE_DB) -> int:
    if not db_path.exists():
        print(f"queue.db not found at {db_path}", file=sys.stderr)
        return 1

    # Import inside main so the script doesn't import dagster (orchestrators
    # is heavy + depends on env that may not exist on the migration host).
    from domains.queue_store.sources import create_schema

    print(f"Running create_schema on {db_path} (idempotent)...")
    create_schema(db_path=db_path)

    seeded = _seed_legacy_rows(db_path)
    print(f"Seeded {seeded} legacy queue_items rows into extraction_calls.")

    print("Migration complete.")
    print(
        "\nNext: re-trigger Dagster `extract_complex_contents/extracted` on the\n"
        "seeded partitions so they pick up the three-call shape:\n"
        "  - clear `extracted_at` for those notion_page_ids (will fire the\n"
        "    re-extract sensor when implemented), OR\n"
        "  - re-materialise per partition from the Dagster UI."
    )
    return 0


def _seed_legacy_rows(db_path: Path) -> int:
    """For each extracted queue_items row with no extraction_calls entries
    yet, insert one seed row carrying the legacy blob + provenance and
    update the cohort summary columns."""
    seeded = 0
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row

        # Candidates: extracted under the old single-shot path, and not yet
        # seen by extraction_calls.
        rows = conn.execute(
            """
            SELECT q.notion_page_id, q.extraction_payload, q.extraction_prompt_label,
                   q.extraction_model, q.prompt_sha256, q.tokens_in, q.tokens_out,
                   q.extracted_at
              FROM queue_items q
             WHERE q.extracted_at IS NOT NULL
               AND q.extraction_payload IS NOT NULL
               AND NOT EXISTS (
                   SELECT 1 FROM extraction_calls c WHERE c.notion_page_id = q.notion_page_id
               )
            """
        ).fetchall()

        for row in rows:
            conn.execute(
                """
                INSERT INTO extraction_calls (
                    notion_page_id, call_kind, prompt_label, prompt_sha256,
                    schema_name, model, output, tokens_in, tokens_out,
                    cached_tokens, duration_ms, extracted_at, node_metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["notion_page_id"],
                    "legacy_v5",
                    row["extraction_prompt_label"] or "legacy_v5",
                    row["prompt_sha256"] or "0" * 64,
                    None,
                    row["extraction_model"] or "unknown",
                    row["extraction_payload"],
                    row["tokens_in"] or 0,
                    row["tokens_out"] or 0,
                    None,
                    None,
                    row["extracted_at"],
                    None,
                ),
            )
            conn.execute(
                """
                UPDATE queue_items SET
                    extractor_label = 'legacy_v5',
                    extractor_sha256 = ?,
                    tokens_in_total = COALESCE(?, 0),
                    tokens_out_total = COALESCE(?, 0)
                  WHERE notion_page_id = ?
                """,
                (
                    row["prompt_sha256"] or "0" * 64,
                    row["tokens_in"],
                    row["tokens_out"],
                    row["notion_page_id"],
                ),
            )
            seeded += 1
    return seeded


if __name__ == "__main__":
    db_arg = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_QUEUE_DB
    sys.exit(main(db_arg))
