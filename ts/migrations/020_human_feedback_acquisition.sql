ALTER TABLE human_feedback ADD COLUMN acquisition_id TEXT;
ALTER TABLE human_feedback ADD COLUMN reviewer TEXT;
ALTER TABLE human_feedback ADD COLUMN criterion_scores_json TEXT NOT NULL DEFAULT '{}';
CREATE UNIQUE INDEX IF NOT EXISTS idx_human_feedback_acquisition_id
    ON human_feedback(acquisition_id) WHERE acquisition_id IS NOT NULL;
