-- Phase 5b of the content-shape rollout.
-- Adds prompt_set_shape to extraction_calls so per-row eval queries can
-- group by the PromptBundle that fired (independent of the shape value
-- on queue_items, which may drift via re-classification).
ALTER TABLE extraction_calls ADD COLUMN prompt_set_shape TEXT;
