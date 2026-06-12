-- Add content_shape + enrichment_json columns to queue_items on
-- already-deployed installs.
--
-- Phase 1 of the content-shape rollout (ai-plannings/2026-06-10_triage-enrich-content-shape.md).
-- Fresh installs create the columns via CREATE TABLE in queue_store.sources;
-- the same `_DDL_IDEMPOTENT` ALTER loop in create_schema() handles existing
-- prod DBs on dagster-code restart. This file mirrors that migration as
-- standalone SQL so a manual apply / pre-deploy verification path exists.
--
-- Usage:
--   ssh hcloud
--   sqlite3 /path/to/queue.db < 2026-06-12_queue_items_content_shape.sql
--
-- Re-running yields "duplicate column name" — the safe failure mode.

ALTER TABLE queue_items ADD COLUMN content_shape TEXT;
ALTER TABLE queue_items ADD COLUMN enrichment_json TEXT;
