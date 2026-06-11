import type Database from "better-sqlite3";

import type { TaskQueueRow } from "./storage-contracts.js";
import {
  completeTaskRecord,
  countPendingTaskRecords,
  dequeueTaskRecord,
  enqueueTaskRecord,
  failTaskRecord,
  getTaskRecord,
  listTaskRecords,
} from "./task-queue-store.js";

export function enqueueStoreTask(
  db: Database.Database,
  id: string,
  specName: string,
  priority = 0,
  config?: Record<string, unknown>,
  scheduledAt?: string,
): void {
  enqueueTaskRecord(db, id, specName, priority, config, scheduledAt);
}

export function dequeueStoreTask(
  db: Database.Database,
): TaskQueueRow | null {
  return dequeueTaskRecord<TaskQueueRow>(db);
}

export function completeStoreTask(
  db: Database.Database,
  taskId: string,
  bestScore: number,
  bestOutput: string,
  totalRounds: number,
  metThreshold: boolean,
  resultJson?: string,
): void {
  completeTaskRecord(
    db,
    taskId,
    bestScore,
    bestOutput,
    totalRounds,
    metThreshold,
    resultJson,
  );
}

export function failStoreTask(
  db: Database.Database,
  taskId: string,
  error: string,
): void {
  failTaskRecord(db, taskId, error);
}

export function countPendingStoreTasks(
  db: Database.Database,
): number {
  return countPendingTaskRecords(db);
}

export function getStoreTask(
  db: Database.Database,
  taskId: string,
): TaskQueueRow | null {
  return getTaskRecord<TaskQueueRow>(db, taskId);
}

export function listStoreTasks(
  db: Database.Database,
  opts: { status?: string; specName?: string; limit?: number } = {},
): TaskQueueRow[] {
  return listTaskRecords<TaskQueueRow>(db, opts);
}
