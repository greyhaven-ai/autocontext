-- AC-906: retry accounting for the task queue. attempts is incremented
-- at claim time so a handler that kills the process still burns an attempt.
ALTER TABLE task_queue ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0;
