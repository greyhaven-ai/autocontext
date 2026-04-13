import type { TaskQueueRow, SQLiteStore } from "../storage/index.js";

export function buildTaskRunnerModel(defaultModel: string, explicitModel?: string): string {
  return explicitModel || defaultModel;
}

export function dequeueTaskBatch(
  store: SQLiteStore,
  maxTasks: number,
): TaskQueueRow[] {
  const tasks: TaskQueueRow[] = [];
  for (let index = 0; index < maxTasks; index++) {
    const task = store.dequeueTask();
    if (!task) {
      break;
    }
    tasks.push(task);
  }
  return tasks;
}
