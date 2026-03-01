/**
 * SQLite storage for MTS task queue.
 * Uses better-sqlite3 for synchronous access (same as Python's sqlite3).
 */
import Database from "better-sqlite3";
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
export class SQLiteStore {
    db;
    constructor(dbPath) {
        this.db = new Database(dbPath);
        this.db.pragma("journal_mode = WAL");
        this.db.pragma("foreign_keys = ON");
    }
    migrate(migrationsDir) {
        const files = readdirSync(migrationsDir)
            .filter(f => f.endsWith(".sql"))
            .sort();
        for (const file of files) {
            const sql = readFileSync(join(migrationsDir, file), "utf8");
            this.db.exec(sql);
        }
    }
    enqueueTask(id, specName, priority = 0, config, scheduledAt) {
        const configJson = config ? JSON.stringify(config) : null;
        this.db
            .prepare(`INSERT INTO task_queue(id, spec_name, priority, config_json, scheduled_at)
         VALUES (?, ?, ?, ?, ?)`)
            .run(id, specName, priority, configJson, scheduledAt ?? null);
    }
    dequeueTask() {
        const tx = this.db.transaction(() => {
            const row = this.db
                .prepare(`SELECT id FROM task_queue
           WHERE status = 'pending'
             AND (scheduled_at IS NULL OR scheduled_at <= datetime('now'))
           ORDER BY priority DESC, created_at ASC
           LIMIT 1`)
                .get();
            if (!row)
                return null;
            const changes = this.db
                .prepare(`UPDATE task_queue
           SET status = 'running',
               started_at = datetime('now'),
               updated_at = datetime('now')
           WHERE id = ? AND status = 'pending'`)
                .run(row.id);
            if (changes.changes === 0)
                return null;
            return this.db
                .prepare("SELECT * FROM task_queue WHERE id = ?")
                .get(row.id) ?? null;
        });
        return tx();
    }
    completeTask(taskId, bestScore, bestOutput, totalRounds, metThreshold, resultJson) {
        this.db
            .prepare(`UPDATE task_queue
         SET status = 'completed',
             completed_at = datetime('now'),
             updated_at = datetime('now'),
             best_score = ?,
             best_output = ?,
             total_rounds = ?,
             met_threshold = ?,
             result_json = ?
         WHERE id = ?`)
            .run(bestScore, bestOutput, totalRounds, metThreshold ? 1 : 0, resultJson ?? null, taskId);
    }
    failTask(taskId, error) {
        this.db
            .prepare(`UPDATE task_queue
         SET status = 'failed',
             completed_at = datetime('now'),
             updated_at = datetime('now'),
             error = ?
         WHERE id = ?`)
            .run(error, taskId);
    }
    pendingTaskCount() {
        const row = this.db
            .prepare("SELECT COUNT(*) as cnt FROM task_queue WHERE status = 'pending'")
            .get();
        return row.cnt;
    }
    getTask(taskId) {
        return (this.db
            .prepare("SELECT * FROM task_queue WHERE id = ?")
            .get(taskId) ?? null);
    }
    close() {
        this.db.close();
    }
}
//# sourceMappingURL=index.js.map