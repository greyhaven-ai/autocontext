import type Database from "better-sqlite3";

export function enqueueTaskRecord(
  db: Database.Database,
  id: string,
  specName: string,
  priority = 0,
  config?: Record<string, unknown>,
  scheduledAt?: string,
): void {
  const configJson = config ? JSON.stringify(config) : null;
  db.prepare(
    `INSERT INTO task_queue(id, spec_name, priority, config_json, scheduled_at)
     VALUES (?, ?, ?, ?, ?)`,
  ).run(id, specName, priority, configJson, scheduledAt ?? null);
}

export function dequeueTaskRecord<T>(db: Database.Database): T | null {
  const tx = db.transaction(() => {
    const row = db.prepare(
      `SELECT id FROM task_queue
       WHERE status = 'pending'
         AND (scheduled_at IS NULL OR scheduled_at <= datetime('now'))
       ORDER BY priority DESC, created_at ASC
       LIMIT 1`,
    ).get() as { id: string } | undefined;

    if (!row) return null;

    const changes = db.prepare(
      `UPDATE task_queue
       SET status = 'running',
           started_at = datetime('now'),
           updated_at = datetime('now')
       WHERE id = ? AND status = 'pending'`,
    ).run(row.id);

    if (changes.changes === 0) return null;

    return (db.prepare("SELECT * FROM task_queue WHERE id = ?").get(row.id) as T | undefined) ?? null;
  });

  return tx() as T | null;
}

export function completeTaskRecord(
  db: Database.Database,
  taskId: string,
  bestScore: number,
  bestOutput: string,
  totalRounds: number,
  metThreshold: boolean,
  resultJson?: string,
): void {
  db.prepare(
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
  ).run(bestScore, bestOutput, totalRounds, metThreshold ? 1 : 0, resultJson ?? null, taskId);
}

export function failTaskRecord(
  db: Database.Database,
  taskId: string,
  error: string,
): void {
  db.prepare(
    `UPDATE task_queue
     SET status = 'failed',
         completed_at = datetime('now'),
         updated_at = datetime('now'),
         error = ?
     WHERE id = ?`,
  ).run(error, taskId);
}

export function countPendingTaskRecords(db: Database.Database): number {
  const row = db.prepare("SELECT COUNT(*) as cnt FROM task_queue WHERE status = 'pending'").get() as { cnt: number };
  return row.cnt;
}

export function getTaskRecord<T>(db: Database.Database, taskId: string): T | null {
  return ((db.prepare("SELECT * FROM task_queue WHERE id = ?").get(taskId) as T | undefined) ?? null);
}

export function listTaskRecords<T>(
  db: Database.Database,
  opts: { status?: string; specName?: string; limit?: number } = {},
): T[] {
  const clauses: string[] = ["1=1"];
  const params: unknown[] = [];
  if (opts.status) {
    clauses.push("status = ?");
    params.push(opts.status);
  }
  if (opts.specName) {
    clauses.push("spec_name = ?");
    params.push(opts.specName);
  }
  const limit = Number.isInteger(opts.limit) && (opts.limit ?? 0) > 0 ? opts.limit! : 50;
  params.push(limit);
  return db.prepare(
    `SELECT * FROM task_queue WHERE ${clauses.join(" AND ")} ORDER BY created_at DESC LIMIT ?`,
  ).all(...params) as T[];
}
