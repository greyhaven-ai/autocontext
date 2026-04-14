export const QUEUE_HELP_TEXT =
  "autoctx queue -s <spec-name> [-p prompt] [-r rubric] [--priority N] " +
  "[--min-rounds N] [--rlm] [--rlm-turns N]";

export interface QueueCommandValues {
  spec?: string;
  prompt?: string;
  rubric?: string;
  priority?: string;
  "min-rounds"?: string;
  rlm?: boolean;
  "rlm-model"?: string;
  "rlm-turns"?: string;
  "rlm-max-tokens"?: string;
  "rlm-temperature"?: string;
  "rlm-max-stdout"?: string;
  "rlm-timeout-ms"?: string;
  "rlm-memory-mb"?: string;
}

interface SavedQueueScenario {
  taskPrompt?: string;
  rubric?: string;
  referenceContext?: string;
  requiredConcepts?: string[];
  maxRounds?: number;
  qualityThreshold?: number;
}

export interface PlannedQueueCommand {
  specName: string;
  request: {
    taskPrompt?: string;
    rubric?: string;
    referenceContext?: string;
    requiredConcepts?: string[];
    maxRounds?: number;
    qualityThreshold?: number;
    priority: number;
    minRounds?: number;
    rlmEnabled?: boolean;
    rlmModel?: string;
    rlmMaxTurns?: number;
    rlmMaxTokensPerTurn?: number;
    rlmTemperature?: number;
    rlmMaxStdoutChars?: number;
    rlmCodeTimeoutMs?: number;
    rlmMemoryLimitMb?: number;
  };
}

export function getQueueUsageExitCode(help: boolean): number {
  return help ? 0 : 1;
}

export function planQueueCommand(
  values: QueueCommandValues,
  savedScenario: SavedQueueScenario | null,
): PlannedQueueCommand {
  if (!values.spec) {
    throw new Error("Queue spec is required");
  }

  return {
    specName: values.spec,
    request: {
      taskPrompt: values.prompt ?? savedScenario?.taskPrompt,
      rubric: values.rubric ?? savedScenario?.rubric,
      referenceContext: savedScenario?.referenceContext,
      requiredConcepts: savedScenario?.requiredConcepts,
      maxRounds: savedScenario?.maxRounds,
      qualityThreshold: savedScenario?.qualityThreshold,
      priority: Number.parseInt(values.priority ?? "0", 10),
      ...(values["min-rounds"]
        ? { minRounds: Number.parseInt(values["min-rounds"], 10) }
        : {}),
      rlmEnabled: values.rlm,
      rlmModel: values["rlm-model"],
      ...(values["rlm-turns"]
        ? { rlmMaxTurns: Number.parseInt(values["rlm-turns"], 10) }
        : {}),
      ...(values["rlm-max-tokens"]
        ? { rlmMaxTokensPerTurn: Number.parseInt(values["rlm-max-tokens"], 10) }
        : {}),
      ...(values["rlm-temperature"]
        ? { rlmTemperature: Number.parseFloat(values["rlm-temperature"]) }
        : {}),
      ...(values["rlm-max-stdout"]
        ? { rlmMaxStdoutChars: Number.parseInt(values["rlm-max-stdout"], 10) }
        : {}),
      ...(values["rlm-timeout-ms"]
        ? { rlmCodeTimeoutMs: Number.parseInt(values["rlm-timeout-ms"], 10) }
        : {}),
      ...(values["rlm-memory-mb"]
        ? { rlmMemoryLimitMb: Number.parseInt(values["rlm-memory-mb"], 10) }
        : {}),
    },
  };
}

export function renderQueuedTaskResult(input: {
  taskId: string;
  specName: string;
}): string {
  return JSON.stringify({
    taskId: input.taskId,
    specName: input.specName,
    status: "queued",
  });
}

export function executeStatusCommandWorkflow(opts: {
  store: {
    migrate(migrationsDir: string): void;
    pendingTaskCount(): number;
    close(): void;
  };
  migrationsDir: string;
}): { pendingCount: number } {
  try {
    opts.store.migrate(opts.migrationsDir);
    return { pendingCount: opts.store.pendingTaskCount() };
  } finally {
    opts.store.close();
  }
}

export function renderStatusResult(result: { pendingCount: number }): string {
  return JSON.stringify(result);
}
