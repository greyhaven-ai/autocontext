import type { TaskQueueRow } from "../storage/index.js";

export interface TaskQueueWorkerStore {
  dequeueTask(): TaskQueueRow | null;
  getTask(taskId: string): TaskQueueRow | null;
  completeTask(
    taskId: string,
    bestScore: number,
    bestOutput: string,
    totalRounds: number,
    metThreshold: boolean,
    resultJson?: string,
  ): void;
  failTask(taskId: string, error: string): void;
}

export interface TaskQueueEnqueueStore extends TaskQueueWorkerStore {
  enqueueTask(
    id: string,
    specName: string,
    priority?: number,
    config?: Record<string, unknown>,
    scheduledAt?: string,
  ): void;
}
