import type Database from "better-sqlite3";

export function createRunRecord(
  db: Database.Database,
  runId: string,
  scenario: string,
  generations: number,
  executorMode: string,
  agentProvider = "",
  minimumGenerations = 1,
): void {
  db.prepare(
    `INSERT OR IGNORE INTO runs(
       run_id,
       scenario,
       minimum_generations,
       target_generations,
       executor_mode,
       status,
       agent_provider
     ) VALUES (?, ?, ?, ?, ?, 'running', ?)`,
  ).run(runId, scenario, minimumGenerations, generations, executorMode, agentProvider);
}

export function getRunRecord<T>(db: Database.Database, runId: string): T | null {
  return ((db.prepare("SELECT * FROM runs WHERE run_id = ?").get(runId) as T | undefined) ?? null);
}

export function updateRunStatusRecord(
  db: Database.Database,
  runId: string,
  status: string,
): void {
  db.prepare(
    `UPDATE runs
     SET status = ?,
         updated_at = datetime('now')
     WHERE run_id = ?`,
  ).run(status, runId);
}

export function countCompletedRunsForScenario(
  db: Database.Database,
  scenario: string,
): number {
  const row = db.prepare(
    `SELECT COUNT(*) as cnt
     FROM runs
     WHERE scenario = ? AND status = 'completed'`,
  ).get(scenario) as { cnt: number };
  return row.cnt;
}

export function listRunRecords<T>(
  db: Database.Database,
  limit = 50,
  scenario?: string,
): T[] {
  if (scenario) {
    return db.prepare(
      `SELECT * FROM runs WHERE scenario = ? ORDER BY created_at DESC LIMIT ?`,
    ).all(scenario, limit) as T[];
  }
  return db.prepare(`SELECT * FROM runs ORDER BY created_at DESC LIMIT ?`).all(limit) as T[];
}

export function listRunRecordsForScenario<T>(
  db: Database.Database,
  scenario: string,
): T[] {
  return db.prepare(
    `SELECT * FROM runs WHERE scenario = ? ORDER BY created_at ASC`,
  ).all(scenario) as T[];
}
