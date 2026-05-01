export const WORKER_HELP_TEXT = `
autoctx worker [--poll-interval seconds] [--concurrency N] [--max-empty-polls N] [--once] [--json]

Run the background task queue worker.

Options:
  --poll-interval N     Seconds to sleep between empty queue polls (default: 60)
  --concurrency N       Maximum queued tasks to process per batch (default: 1)
  --max-empty-polls N   Stop after N empty polls; 0 runs until signaled (default: 0)
  --model MODEL         Judge model override for queued tasks
  --once                Process one batch and exit
  --json                Output structured JSON on exit
`.trim();

export interface WorkerCommandValues {
  "poll-interval"?: string;
  concurrency?: string;
  "max-empty-polls"?: string;
  model?: string;
  once?: boolean;
  json?: boolean;
}

export interface WorkerCommandPlan {
  pollInterval: number;
  concurrency: number;
  maxEmptyPolls: number;
  model?: string;
  once: boolean;
  json: boolean;
}

export function planWorkerCommand(values: WorkerCommandValues): WorkerCommandPlan {
  const pollInterval = parseNonNegativeFloat(
    values["poll-interval"] ?? "60",
    "poll interval",
  );
  const concurrency = parsePositiveInteger(
    values.concurrency ?? "1",
    "concurrency",
  );
  const maxEmptyPolls = parseNonNegativeInteger(
    values["max-empty-polls"] ?? "0",
    "max empty polls",
  );
  const model = values.model?.trim() || undefined;

  return {
    pollInterval,
    concurrency,
    maxEmptyPolls,
    model,
    once: values.once === true,
    json: values.json === true,
  };
}

export function renderWorkerResult(input: {
  mode: "once" | "daemon";
  tasksProcessed: number;
  pollInterval: number;
  concurrency: number;
  json: boolean;
}): string {
  if (input.json) {
    return JSON.stringify({
      status: "stopped",
      mode: input.mode,
      tasksProcessed: input.tasksProcessed,
      pollInterval: input.pollInterval,
      concurrency: input.concurrency,
    });
  }

  return [
    `Worker stopped (${input.mode}).`,
    `Processed ${input.tasksProcessed} task(s).`,
    `Concurrency: ${input.concurrency}.`,
  ].join(" ");
}

function parsePositiveInteger(raw: string, label: string): number {
  const trimmed = raw.trim();
  const parsed = Number.parseInt(trimmed, 10);
  if (!/^\d+$/.test(trimmed) || parsed <= 0) {
    throw new Error(`${label} must be a positive integer`);
  }
  return parsed;
}

function parseNonNegativeInteger(raw: string, label: string): number {
  const trimmed = raw.trim();
  const parsed = Number.parseInt(trimmed, 10);
  if (!/^\d+$/.test(trimmed)) {
    throw new Error(`${label} must be zero or a positive integer`);
  }
  return parsed;
}

function parseNonNegativeFloat(raw: string, label: string): number {
  const trimmed = raw.trim();
  const parsed = Number.parseFloat(trimmed);
  if (
    !/^(?:\d+(?:\.\d+)?|\.\d+)$/.test(trimmed) ||
    !Number.isFinite(parsed) ||
    parsed < 0
  ) {
    throw new Error(`${label} must be non-negative`);
  }
  return parsed;
}
