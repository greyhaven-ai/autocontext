/**
 * SQLite storage for MTS task queue.
 * Uses better-sqlite3 for synchronous access (same as Python's sqlite3).
 */
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
export declare class SQLiteStore {
    private db;
    constructor(dbPath: string);
    migrate(migrationsDir: string): void;
    enqueueTask(id: string, specName: string, priority?: number, config?: Record<string, unknown>, scheduledAt?: string): void;
    dequeueTask(): TaskQueueRow | null;
    completeTask(taskId: string, bestScore: number, bestOutput: string, totalRounds: number, metThreshold: boolean, resultJson?: string): void;
    failTask(taskId: string, error: string): void;
    pendingTaskCount(): number;
    getTask(taskId: string): TaskQueueRow | null;
    close(): void;
}
//# sourceMappingURL=index.d.ts.map