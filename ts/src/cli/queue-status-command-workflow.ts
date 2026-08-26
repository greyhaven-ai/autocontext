import type { AgentTaskSpec } from "../scenarios/agent-task-spec.js";
import {
  NATIVE_AGENT_TASK_QUEUE_MARKER,
  requiresNativeAgentTaskExecution,
  savedAgentTaskSpecDigest,
} from "../scenarios/saved-agent-task-routing.js";

export const QUEUE_HELP_TEXT = `autoctx queue — add work to the background queue

Usage:
  autoctx queue add --spec <name> [options]
  autoctx queue --spec <name> [options]  (legacy form)
  autoctx queue status [--json]

Options:
  -s, --spec <name>       Saved task specification to queue
  -p, --prompt <text>     Override the task prompt
  -r, --rubric <text>     Override the evaluation rubric
  --priority <N>          Queue priority (default: 0)
  --min-rounds <N>        Minimum improvement rounds (default: 1)
  --browser-url <url>     Browser evidence URL
  --rlm                   Enable the iterative runtime
  --rlm-model <name>      Model override for the iterative runtime
  --rlm-turns <N>         Maximum iterative-runtime turns
  --rlm-max-tokens <N>    Maximum tokens per turn
  --rlm-temperature <N>   Sampling temperature
  --rlm-max-stdout <N>    Maximum captured stdout characters
  --rlm-timeout-ms <N>    Code execution timeout in milliseconds
  --rlm-memory-mb <N>     Code execution memory limit in MiB

Next: autoctx queue status`;

export interface QueueCommandValues {
  spec?: string;
  prompt?: string;
  rubric?: string;
  "browser-url"?: string;
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
  agentTaskSpec?: AgentTaskSpec;
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
    browserUrl?: string;
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
    nativeTaskMarker?: typeof NATIVE_AGENT_TASK_QUEUE_MARKER;
    savedSpecDigest?: string;
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
  const nativeAgentTaskSpec =
    savedScenario?.agentTaskSpec && requiresNativeAgentTaskExecution(savedScenario.agentTaskSpec)
      ? savedScenario.agentTaskSpec
      : undefined;
  if (nativeAgentTaskSpec && values.rlm) {
    throw new Error(
      "--rlm is not supported for saved structured tasks because queued execution must retain the native evaluator-isolated revision path",
    );
  }

  return {
    specName: values.spec,
    request: {
      taskPrompt: values.prompt ?? (nativeAgentTaskSpec ? undefined : savedScenario?.taskPrompt),
      rubric: values.rubric ?? (nativeAgentTaskSpec ? undefined : savedScenario?.rubric),
      browserUrl: values["browser-url"],
      referenceContext: nativeAgentTaskSpec ? undefined : savedScenario?.referenceContext,
      requiredConcepts: nativeAgentTaskSpec ? undefined : savedScenario?.requiredConcepts,
      maxRounds: nativeAgentTaskSpec ? undefined : savedScenario?.maxRounds,
      qualityThreshold: nativeAgentTaskSpec ? undefined : savedScenario?.qualityThreshold,
      priority: Number.parseInt(values.priority ?? "0", 10),
      ...(values["min-rounds"] ? { minRounds: Number.parseInt(values["min-rounds"], 10) } : {}),
      rlmEnabled: values.rlm,
      rlmModel: values["rlm-model"],
      ...(values["rlm-turns"] ? { rlmMaxTurns: Number.parseInt(values["rlm-turns"], 10) } : {}),
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
      ...(nativeAgentTaskSpec
        ? {
            nativeTaskMarker: NATIVE_AGENT_TASK_QUEUE_MARKER,
            savedSpecDigest: savedAgentTaskSpecDigest(nativeAgentTaskSpec),
          }
        : {}),
    },
  };
}

export function renderQueuedTaskResult(input: { taskId: string; specName: string }): string {
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
  return JSON.stringify({
    pending_count: result.pendingCount,
    // Compatibility field retained for existing npm CLI consumers.
    pendingCount: result.pendingCount,
  });
}
