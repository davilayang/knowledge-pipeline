-- Add raw_content_override column to queue_items on already-deployed installs.
--
-- Run once on each prod queue.db before deploying the structurer feature.
-- Fresh installs create the column via CREATE TABLE in queue_store.sources.
--
-- Usage:
--   ssh hcloud
--   sqlite3 /path/to/queue.db < 2026-06-10_queue_items_raw_content_override.sql
--
-- Re-running yields a "duplicate column name" error — the safe failure mode.

ALTER TABLE queue_items ADD COLUMN raw_content_override TEXT NOT NULL DEFAULT '';
