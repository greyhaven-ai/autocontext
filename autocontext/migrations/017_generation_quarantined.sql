-- AC-885 Slice C1: quarantine marker for scores produced under a non-active evaluator epoch.
ALTER TABLE generations ADD COLUMN quarantined INTEGER DEFAULT NULL;
