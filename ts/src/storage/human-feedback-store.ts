import type Database from "better-sqlite3";

export interface HumanFeedbackRecord {
  id: number;
  scenario_name: string;
  generation_id: string | null;
  agent_output: string;
  human_score: number | null;
  human_notes: string;
  created_at: string;
}

export function insertHumanFeedbackRecord(
  db: Database.Database,
  scenarioName: string,
  agentOutput: string,
  humanScore?: number | null,
  humanNotes = "",
  generationId?: string | null,
): number {
  if (humanScore != null && (humanScore < 0 || humanScore > 1)) {
    throw new Error(`human_score must be in [0.0, 1.0], got ${humanScore}`);
  }

  const result = db
    .prepare(
      `INSERT INTO human_feedback(scenario_name, generation_id, agent_output, human_score, human_notes)
       VALUES (?, ?, ?, ?, ?)`,
    )
    .run(scenarioName, generationId ?? null, agentOutput, humanScore ?? null, humanNotes);

  return Number(result.lastInsertRowid);
}

export function getHumanFeedbackRecords<TRow extends HumanFeedbackRecord>(
  db: Database.Database,
  scenarioName: string,
  limit = 10,
): TRow[] {
  return db
    .prepare(
      `SELECT id, scenario_name, generation_id, agent_output, human_score, human_notes, created_at
       FROM human_feedback
       WHERE scenario_name = ?
       ORDER BY created_at DESC, id DESC
       LIMIT ?`,
    )
    .all(scenarioName, limit) as TRow[];
}

export function getCalibrationExampleRecords<TRow extends HumanFeedbackRecord>(
  db: Database.Database,
  scenarioName: string,
  limit = 5,
): TRow[] {
  return db
    .prepare(
      `SELECT id, scenario_name, agent_output, human_score, human_notes, created_at
       FROM human_feedback
       WHERE scenario_name = ? AND human_score IS NOT NULL AND human_notes != ''
         AND acquisition_id IS NULL
       ORDER BY created_at DESC, id DESC
       LIMIT ?`,
    )
    .all(scenarioName, limit) as TRow[];
}
