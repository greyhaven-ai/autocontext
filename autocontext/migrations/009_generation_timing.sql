-- AC-174: Track per-generation wall-clock duration.
ALTER TABLE generations ADD COLUMN duration_seconds REAL DEFAULT NULL;
