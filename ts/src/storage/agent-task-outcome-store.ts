import type Database from "better-sqlite3";

import type { AgentTaskOutcomeRow } from "./storage-contracts.js";

export function saveAgentTaskOutcomeRecord(
  db: Database.Database,
  runId: string,
  outcomeJson: string,
): void {
  db.prepare(
    `INSERT INTO agent_task_outcomes(run_id, outcome_json)
     VALUES (?, ?)
     ON CONFLICT(run_id) DO UPDATE SET
       outcome_json = excluded.outcome_json,
       updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')`,
  ).run(runId, outcomeJson);
}

export function getAgentTaskOutcomeRecord(
  db: Database.Database,
  runId: string,
): AgentTaskOutcomeRow | null {
  return (
    (db.prepare("SELECT * FROM agent_task_outcomes WHERE run_id = ?").get(runId) as
      AgentTaskOutcomeRow | undefined) ?? null
  );
}
