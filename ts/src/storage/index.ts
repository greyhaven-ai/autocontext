/**
 * SQLite storage for autocontext task queue.
 * Uses better-sqlite3 for synchronous access (same as Python's sqlite3).
 */

import Database from "better-sqlite3";
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";

export interface TaskQueueRow {
  id: string;
  spec_name: string;
  status: string;
  priority: number;
  config_json: string | null;
  scheduled_at: string | null;
  started_at: string | null;
  completed_at: string | null;
  best_score: number | null;
  best_output: string | null;
  total_rounds: number | null;
  met_threshold: number;
  result_json: string | null;
  error: string | null;
  created_at: string;
  updated_at: string;
}

export interface HumanFeedbackRow {
  id: number;
  scenario_name: string;
  generation_id: string | null;
  agent_output: string;
  human_score: number | null;
  human_notes: string;
  created_at: string;
}

export class SQLiteStore {
  private db: Database.Database;

  constructor(dbPath: string) {
    this.db = new Database(dbPath);
    this.db.pragma("journal_mode = WAL");
    this.db.pragma("foreign_keys = ON");
  }

  migrate(migrationsDir: string): void {
    this.db.exec(
      `CREATE TABLE IF NOT EXISTS schema_version (
         filename TEXT PRIMARY KEY,
         applied_at TEXT NOT NULL DEFAULT (datetime('now'))
       )`,
    );

    const applied = new Set(
      (this.db.prepare("SELECT filename FROM schema_version").all() as Array<{ filename: string }>)
        .map(r => r.filename),
    );

    const files = readdirSync(migrationsDir)
      .filter(f => f.endsWith(".sql"))
      .sort();

    for (const file of files) {
      if (applied.has(file)) continue;
      const sql = readFileSync(join(migrationsDir, file), "utf8");
      this.db.exec(sql);
      this.db.prepare("INSERT INTO schema_version(filename) VALUES (?)").run(file);
    }
  }

  enqueueTask(
    id: string,
    specName: string,
    priority = 0,
    config?: Record<string, unknown>,
    scheduledAt?: string,
  ): void {
    const configJson = config ? JSON.stringify(config) : null;
    this.db
      .prepare(
        `INSERT INTO task_queue(id, spec_name, priority, config_json, scheduled_at)
         VALUES (?, ?, ?, ?, ?)`,
      )
      .run(id, specName, priority, configJson, scheduledAt ?? null);
  }

  dequeueTask(): TaskQueueRow | null {
    const tx = this.db.transaction(() => {
      const row = this.db
        .prepare(
          `SELECT id FROM task_queue
           WHERE status = 'pending'
             AND (scheduled_at IS NULL OR scheduled_at <= datetime('now'))
           ORDER BY priority DESC, created_at ASC
           LIMIT 1`,
        )
        .get() as { id: string } | undefined;

      if (!row) return null;

      const changes = this.db
        .prepare(
          `UPDATE task_queue
           SET status = 'running',
               started_at = datetime('now'),
               updated_at = datetime('now')
           WHERE id = ? AND status = 'pending'`,
        )
        .run(row.id);

      if (changes.changes === 0) return null;

      return this.db
        .prepare("SELECT * FROM task_queue WHERE id = ?")
        .get(row.id) as TaskQueueRow | undefined ?? null;
    });

    return tx();
  }

  completeTask(
    taskId: string,
    bestScore: number,
    bestOutput: string,
    totalRounds: number,
    metThreshold: boolean,
    resultJson?: string,
  ): void {
    this.db
      .prepare(
        `UPDATE task_queue
         SET status = 'completed',
             completed_at = datetime('now'),
             updated_at = datetime('now'),
             best_score = ?,
             best_output = ?,
             total_rounds = ?,
             met_threshold = ?,
             result_json = ?
         WHERE id = ?`,
      )
      .run(bestScore, bestOutput, totalRounds, metThreshold ? 1 : 0, resultJson ?? null, taskId);
  }

  failTask(taskId: string, error: string): void {
    this.db
      .prepare(
        `UPDATE task_queue
         SET status = 'failed',
             completed_at = datetime('now'),
             updated_at = datetime('now'),
             error = ?
         WHERE id = ?`,
      )
      .run(error, taskId);
  }

  pendingTaskCount(): number {
    const row = this.db
      .prepare("SELECT COUNT(*) as cnt FROM task_queue WHERE status = 'pending'")
      .get() as { cnt: number };
    return row.cnt;
  }

  getTask(taskId: string): TaskQueueRow | null {
    return (
      (this.db
        .prepare("SELECT * FROM task_queue WHERE id = ?")
        .get(taskId) as TaskQueueRow | undefined) ?? null
    );
  }

  // ---- Human Feedback ----

  insertHumanFeedback(
    scenarioName: string,
    agentOutput: string,
    humanScore?: number | null,
    humanNotes = "",
    generationId?: string | null,
  ): number {
    if (humanScore != null && (humanScore < 0 || humanScore > 1)) {
      throw new Error(`human_score must be in [0.0, 1.0], got ${humanScore}`);
    }
    const result = this.db
      .prepare(
        `INSERT INTO human_feedback(scenario_name, generation_id, agent_output, human_score, human_notes)
         VALUES (?, ?, ?, ?, ?)`,
      )
      .run(scenarioName, generationId ?? null, agentOutput, humanScore ?? null, humanNotes);
    return Number(result.lastInsertRowid);
  }

  getHumanFeedback(scenarioName: string, limit = 10): HumanFeedbackRow[] {
    return this.db
      .prepare(
        `SELECT id, scenario_name, generation_id, agent_output, human_score, human_notes, created_at
         FROM human_feedback
         WHERE scenario_name = ?
         ORDER BY created_at DESC
         LIMIT ?`,
      )
      .all(scenarioName, limit) as HumanFeedbackRow[];
  }

  getCalibrationExamples(scenarioName: string, limit = 5): HumanFeedbackRow[] {
    return this.db
      .prepare(
        `SELECT id, scenario_name, agent_output, human_score, human_notes, created_at
         FROM human_feedback
         WHERE scenario_name = ? AND human_score IS NOT NULL AND human_notes != ''
         ORDER BY created_at DESC
         LIMIT ?`,
      )
      .all(scenarioName, limit) as HumanFeedbackRow[];
  }

  close(): void {
    this.db.close();
  }
}
