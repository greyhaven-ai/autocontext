import type { TaskQueueRow } from "../storage/index.js";
import type { TaskQueueWorkerStore } from "./task-queue-store.js";

export function buildTaskRunnerModel(defaultModel: string, explicitModel?: string): string {
  return explicitModel || defaultModel;
}

export async function dequeueTaskBatch(
  store: Pick<TaskQueueWorkerStore, "dequeueTask">,
  maxTasks: number,
): Promise<TaskQueueRow[]> {
  const tasks: TaskQueueRow[] = [];
  for (let index = 0; index < maxTasks; index++) {
    const task = await store.dequeueTask();
    if (!task) {
      break;
    }
    tasks.push(task);
  }
  return tasks;
}
