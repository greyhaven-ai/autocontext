export const SCHEMA_PARITY_SHARED_TABLES = [
  "agent_outputs",
  "agent_role_metrics",
  "consultation_log",
  "generation_recovery",
  "generation_score_revisions",
  "generations",
  "hub_packages",
  "hub_promotions",
  "hub_results",
  "hub_sessions",
  "human_feedback",
  "knowledge_snapshots",
  "matches",
  "monitor_alerts",
  "monitor_conditions",
  "runs",
  "session_notebooks",
  "task_queue",
] as const;

export const SCHEMA_PARITY_PYTHON_ONLY_TABLES = [
  {
    table: "staged_validation_results",
    reason: "Python-only staged validation metadata until TypeScript staged validation is ported.",
  },
] as const;

export const SCHEMA_PARITY_TYPESCRIPT_ONLY_TABLES = [
  {
    table: "agent_task_outcomes",
    reason:
      "TypeScript-only structured agent-task outcome contract; Python does not advertise structured tasks.",
  },
] as const;

export const SCHEMA_PARITY_LEDGER_TABLES = ["schema_migrations", "schema_version"] as const;
