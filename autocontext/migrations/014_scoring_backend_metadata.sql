-- AC-319: persist scoring backend selection and uncertainty metadata.
ALTER TABLE generations ADD COLUMN scoring_backend TEXT NOT NULL DEFAULT 'elo';
ALTER TABLE generations ADD COLUMN rating_uncertainty REAL DEFAULT NULL;

ALTER TABLE knowledge_snapshots ADD COLUMN scoring_backend TEXT NOT NULL DEFAULT 'elo';
ALTER TABLE knowledge_snapshots ADD COLUMN rating_uncertainty REAL DEFAULT NULL;
