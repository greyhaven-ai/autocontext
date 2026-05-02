import type { TaskQueueRow } from "../storage/index.js";

export type MaybePromise<T> = T | Promise<T>;

export interface TaskQueueWorkerStore {
  dequeueTask(): MaybePromise<TaskQueueRow | null>;
  getTask(taskId: string): MaybePromise<TaskQueueRow | null>;
  completeTask(
    taskId: string,
    bestScore: number,
    bestOutput: string,
    totalRounds: number,
    metThreshold: boolean,
    resultJson?: string,
  ): MaybePromise<void>;
  failTask(taskId: string, error: string): MaybePromise<void>;
}

export interface TaskQueueEnqueueStore extends TaskQueueWorkerStore {
  enqueueTask(
    id: string,
    specName: string,
    priority?: number,
    config?: Record<string, unknown>,
    scheduledAt?: string,
  ): MaybePromise<void>;
}
