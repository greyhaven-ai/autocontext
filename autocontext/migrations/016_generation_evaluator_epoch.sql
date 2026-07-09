-- AC-885 Slice B: evaluator-epoch lineage on judge-scored generation rows.
-- Null for tournament-scored rows and legacy rows.
ALTER TABLE generations ADD COLUMN evaluator_epoch TEXT DEFAULT NULL;
