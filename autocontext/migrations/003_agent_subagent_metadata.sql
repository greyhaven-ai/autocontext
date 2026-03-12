ALTER TABLE agent_role_metrics ADD COLUMN subagent_id TEXT NOT NULL DEFAULT '';
ALTER TABLE agent_role_metrics ADD COLUMN status TEXT NOT NULL DEFAULT 'completed';
