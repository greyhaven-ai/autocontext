ALTER TABLE runs ADD COLUMN agent_provider TEXT NOT NULL DEFAULT '';
ALTER TABLE knowledge_snapshots ADD COLUMN agent_provider TEXT NOT NULL DEFAULT '';
ALTER TABLE knowledge_snapshots ADD COLUMN rlm_enabled INTEGER NOT NULL DEFAULT 0;
