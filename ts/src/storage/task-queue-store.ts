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
  // AC-906: single-statement claim (the ambient-queue idiom); attempts is
  // burned AT CLAIM so a handler that kills the process still counts toward
  // the dead-letter limit. Mirrors the Python store.
  const row = db.prepare(
    `UPDATE task_queue
     SET status = 'running',
         started_at = datetime('now'),
         updated_at = datetime('now'),
         attempts = attempts + 1
     WHERE id = (
       SELECT id FROM task_queue
       WHERE status = 'pending'
         AND (scheduled_at IS NULL OR scheduled_at <= datetime('now'))
       ORDER BY priority DESC, created_at ASC
       LIMIT 1
     )
     RETURNING *`,
  ).get() as T | undefined;
  return row ?? null;
}

/**
 * Return crash-stranded running tasks to pending (AC-906). The claim-time
 * attempts increment already counted the stranded execution, so the
 * dead-letter limit still applies across crash-loops.
 */
export function requeueStaleRunning(
  db: Database.Database,
  olderThanSeconds: number,
  maxAttempts?: number,
): number {
  const limit = maxAttempts ?? 2 ** 31;
  const result = db.prepare(
    `UPDATE task_queue
     SET status = CASE WHEN attempts >= ? THEN 'failed' ELSE 'pending' END,
         error = CASE
           WHEN attempts >= ? THEN COALESCE(error, 'crash-looped past the attempts limit')
           ELSE error
         END,
         updated_at = datetime('now')
     WHERE status = 'running'
       AND started_at IS NOT NULL
       AND (julianday('now') - julianday(started_at)) * 86400.0 >= ?`,
  ).run(limit, limit, olderThanSeconds);
  return result.changes;
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
  maxAttempts?: number,
  retryBackoffS = 30,
): void {
  if (maxAttempts !== undefined) {
    // AC-906: below the attempts limit the task requeues for a later retry
    // after a linear backoff (capped at 300s) so an outage is not burned
    // through in seconds; at or above it the task dead-letters. Mirrors
    // Python's fail_task.
    db.prepare(
      `UPDATE task_queue
       SET status = CASE WHEN attempts >= ? THEN 'failed' ELSE 'pending' END,
           completed_at = CASE WHEN attempts >= ? THEN datetime('now') ELSE NULL END,
           scheduled_at = CASE
             WHEN attempts >= ? THEN scheduled_at
             ELSE datetime('now', '+' || CAST(min(300.0, ? * attempts) AS TEXT) || ' seconds')
           END,
           updated_at = datetime('now'),
           error = ?
       WHERE id = ?`,
    ).run(maxAttempts, maxAttempts, maxAttempts, retryBackoffS, error, taskId);
    return;
  }
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
