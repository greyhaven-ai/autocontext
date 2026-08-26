import type { AgentTaskSpec } from "../scenarios/agent-task-spec.js";
import type { AgentTaskInterface } from "../types/index.js";
import {
  overrideSavedAgentTaskSpec,
  requiresNativeAgentTaskExecution,
} from "../scenarios/saved-agent-task-routing.js";

export const IMPROVE_HELP_TEXT = `autoctx improve — Run multi-round improvement loop

Usage: autoctx improve [options]

Options:
  -s, --scenario <name>   Use a saved custom scenario (provides prompt + rubric)
  -p, --prompt <text>     Task prompt
  -o, --output <text>     Initial agent output to improve (optional; generated if omitted)
  -r, --rubric <text>     Evaluation rubric/criteria
  -n, --rounds N          Maximum improvement rounds (default: 5)
  -t, --threshold N       Quality threshold to stop early (default: 0.9)
  --min-rounds N          Minimum rounds before early stop (default: 1)
  --rlm                   Use REPL-loop mode (agent writes + runs code)
  --rlm-model <name>      Model override for the iterative runtime
  --rlm-turns N           Max REPL turns per round
  --rlm-max-tokens N      Maximum tokens per iterative-runtime turn
  --rlm-temperature N     Sampling temperature for iterative-runtime turns
  --rlm-max-stdout N      Maximum captured stdout characters
  --rlm-timeout-ms N      Code execution timeout in milliseconds
  --rlm-memory-mb N       Code execution memory limit in MiB
  -v, --verbose           Show detailed round-by-round output

Provide either --scenario or both --prompt and --rubric.

Examples:
  autoctx improve -p "Write a summary" -r "Score clarity" -n 3
  autoctx improve -p "Write a summary" -o "Draft here" -r "Score clarity" -n 3
  autoctx improve -s my_task --threshold 0.95

See also: judge, queue, run`;

export interface ImproveCommandValues {
  scenario?: string;
  prompt?: string;
  output?: string;
  rubric?: string;
  rounds?: string;
  threshold?: string;
  "min-rounds"?: string;
  rlm?: boolean;
  "rlm-model"?: string;
  "rlm-turns"?: string;
  "rlm-max-tokens"?: string;
  "rlm-temperature"?: string;
  "rlm-max-stdout"?: string;
  "rlm-timeout-ms"?: string;
  "rlm-memory-mb"?: string;
  verbose?: boolean;
  help?: boolean;
}

export interface ImproveSavedScenario {
  name?: string;
  agentTaskSpec?: AgentTaskSpec;
  taskPrompt?: string;
  rubric?: string;
  maxRounds?: number;
  qualityThreshold?: number;
  revisionPrompt?: string | null;
  referenceContext?: string;
  requiredConcepts?: string[];
  calibrationExamples?: Record<string, unknown>[];
}

export interface ImprovePlan {
  taskPrompt: string;
  rubric: string;
  maxRounds: number;
  qualityThreshold: number;
  minRounds: number;
  initialOutput?: string;
  verbose: boolean;
  revisionPrompt?: string | null;
  rlmConfig: Record<string, unknown>;
  nativeAgentTask?: {
    name: string;
    spec: AgentTaskSpec;
  };
}

export interface ImproveWorkflowResult {
  totalRounds: number;
  metThreshold: boolean;
  bestScore: number;
  bestRound: number;
  judgeFailures: number;
  terminationReason: string;
  totalInternalRetries: number;
  dimensionTrajectory: unknown;
  bestOutput: string;
  durationMs: number;
  rounds: Array<{
    roundNumber: number;
    score: number;
    dimensionScores: Record<string, number>;
    reasoning: string;
    isRevision: boolean;
    judgeFailed: boolean;
  }>;
  rlmSessions?: unknown[];
}

export function getImproveUsageExitCode(
  values: Pick<ImproveCommandValues, "help" | "scenario" | "prompt" | "rubric" | "output" | "rlm">,
): 0 | 1 | null {
  if (values.help) return 0;
  if (!values.scenario && (!values.prompt || !values.rubric)) {
    return 1;
  }
  return null;
}

export function planImproveCommand(
  values: ImproveCommandValues,
  savedScenario: ImproveSavedScenario | null,
  parsePositiveInteger: (raw: string, label: string) => number,
): ImprovePlan {
  const taskPrompt = values.prompt ?? savedScenario?.taskPrompt;
  const rubric = values.rubric ?? savedScenario?.rubric;
  if (!taskPrompt || !rubric) {
    throw new Error(
      "Error: improve requires either --scenario <name> or both --prompt and --rubric.",
    );
  }

  const maxRounds = values.rounds
    ? parsePositiveInteger(values.rounds, "--rounds")
    : (savedScenario?.maxRounds ?? 5);
  const qualityThreshold = values.threshold
    ? Number.parseFloat(values.threshold)
    : (savedScenario?.qualityThreshold ?? 0.9);
  const revisionPrompt = savedScenario?.revisionPrompt;

  const nativeAgentTask =
    savedScenario?.agentTaskSpec && requiresNativeAgentTaskExecution(savedScenario.agentTaskSpec)
      ? {
          name: savedScenario.name ?? values.scenario ?? "saved-agent-task",
          spec: {
            ...overrideSavedAgentTaskSpec(savedScenario.agentTaskSpec, {
              taskPrompt: values.prompt,
              judgeRubric: values.rubric,
            }),
            maxRounds,
            qualityThreshold,
            revisionPrompt: revisionPrompt ?? undefined,
          },
        }
      : undefined;
  if (nativeAgentTask && values.rlm) {
    throw new Error(
      "--rlm is not supported for saved structured tasks because it bypasses the native evaluator-isolated revision path",
    );
  }

  return {
    taskPrompt,
    rubric,
    maxRounds,
    qualityThreshold,
    minRounds: values["min-rounds"]
      ? parsePositiveInteger(values["min-rounds"], "--min-rounds")
      : 1,
    initialOutput: values.output,
    verbose: !!values.verbose,
    revisionPrompt,
    rlmConfig: {
      enabled: values.rlm ?? false,
      model: values["rlm-model"],
      ...(values["rlm-turns"] ? { maxTurns: Number.parseInt(values["rlm-turns"], 10) } : {}),
      ...(values["rlm-max-tokens"]
        ? { maxTokensPerTurn: Number.parseInt(values["rlm-max-tokens"], 10) }
        : {}),
      ...(values["rlm-temperature"]
        ? { temperature: Number.parseFloat(values["rlm-temperature"]) }
        : {}),
      ...(values["rlm-max-stdout"]
        ? { maxStdoutChars: Number.parseInt(values["rlm-max-stdout"], 10) }
        : {}),
      ...(values["rlm-timeout-ms"]
        ? { codeTimeoutMs: Number.parseInt(values["rlm-timeout-ms"], 10) }
        : {}),
      ...(values["rlm-memory-mb"]
        ? { memoryLimitMb: Number.parseInt(values["rlm-memory-mb"], 10) }
        : {}),
    },
    ...(nativeAgentTask ? { nativeAgentTask } : {}),
  };
}

interface ImproveWorkflowTask extends Partial<AgentTaskInterface> {
  generateOutput(args: {
    referenceContext?: string;
    requiredConcepts?: string[];
    state?: Record<string, unknown>;
  }): Promise<string>;
  getRlmSessions(): unknown[];
  initialState?(seed?: number): Record<string, unknown>;
  prepareContext?(state: Record<string, unknown>): Promise<Record<string, unknown>>;
  validateContext?(state: Record<string, unknown>): string[];
}

export async function executeImproveCommandWorkflow<
  TLoop extends {
    run(args: {
      initialOutput: string;
      state: Record<string, unknown>;
      referenceContext?: string;
      requiredConcepts?: string[];
      calibrationExamples?: Record<string, unknown>[];
    }): Promise<Omit<ImproveWorkflowResult, "durationMs" | "rlmSessions">>;
  },
>(opts: {
  plan: ImprovePlan;
  provider: unknown;
  model: string | null;
  savedScenario: ImproveSavedScenario | null;
  createTask: (
    taskPrompt: string,
    rubric: string,
    provider: unknown,
    model: string | null,
    revisionPrompt: string | null | undefined,
    rlmConfig: Record<string, unknown>,
    candidateGrounding: boolean,
  ) => ImproveWorkflowTask;
  createStructuredTask?: (args: {
    name: string;
    spec: AgentTaskSpec;
    provider: unknown;
    model: string | null;
  }) => ImproveWorkflowTask;
  createLoop: (args: {
    task: ImproveWorkflowTask;
    maxRounds: number;
    qualityThreshold: number;
    minRounds: number;
  }) => TLoop;
  now: () => number;
}): Promise<ImproveWorkflowResult> {
  if (opts.plan.nativeAgentTask && !opts.createStructuredTask) {
    throw new Error(
      "Saved structured task requires native dual-context improvement; evaluator-only evidence was not sent to a generic improvement loop",
    );
  }
  const candidateGrounding = !opts.savedScenario?.agentTaskSpec
    || Boolean(opts.plan.nativeAgentTask);
  const task = opts.plan.nativeAgentTask
    ? opts.createStructuredTask!({
        ...opts.plan.nativeAgentTask,
        provider: opts.provider,
        model: opts.model,
      })
    : opts.createTask(
        opts.plan.taskPrompt,
        opts.plan.rubric,
        opts.provider,
        opts.model,
        opts.plan.revisionPrompt,
        opts.plan.rlmConfig,
        candidateGrounding,
      );
  const loop = opts.createLoop({
    task,
    maxRounds: opts.plan.maxRounds,
    qualityThreshold: opts.plan.qualityThreshold,
    minRounds: opts.plan.minRounds,
  });

  const startTime = opts.now();
  let state = task.initialState?.() ?? {};
  if (task.prepareContext) {
    state = await task.prepareContext(state);
  }
  const contextErrors = task.validateContext?.(state) ?? [];
  if (contextErrors.length > 0) {
    throw new Error(`agent_task context preparation failed: ${contextErrors.join("; ")}`);
  }
  const initialOutput =
    opts.plan.initialOutput ??
    (await task.generateOutput({
      referenceContext: candidateGrounding
        ? opts.savedScenario?.referenceContext
        : undefined,
      requiredConcepts: candidateGrounding
        ? opts.savedScenario?.requiredConcepts
        : undefined,
      ...(opts.plan.nativeAgentTask ? { state } : {}),
    }));
  const result = await loop.run({
    initialOutput,
    state,
    referenceContext: opts.savedScenario?.referenceContext,
    requiredConcepts: opts.savedScenario?.requiredConcepts,
    calibrationExamples: opts.savedScenario?.calibrationExamples,
  });

  return {
    ...result,
    durationMs: Math.round(opts.now() - startTime),
    ...(task.getRlmSessions().length > 0 ? { rlmSessions: task.getRlmSessions() } : {}),
  };
}

export function renderImproveResult(
  result: ImproveWorkflowResult,
  verbose: boolean,
): { stdout: string; stderrLines: string[] } {
  const stderrLines = verbose
    ? result.rounds.map((round) =>
        JSON.stringify({
          round: round.roundNumber,
          score: round.score,
          dimensionScores: round.dimensionScores,
          reasoning:
            round.reasoning.length > 200 ? `${round.reasoning.slice(0, 200)}...` : round.reasoning,
          isRevision: round.isRevision,
          judgeFailed: round.judgeFailed,
        }),
      )
    : [];

  return {
    stderrLines,
    stdout: JSON.stringify(
      {
        totalRounds: result.totalRounds,
        metThreshold: result.metThreshold,
        bestScore: result.bestScore,
        bestRound: result.bestRound,
        judgeFailures: result.judgeFailures,
        terminationReason: result.terminationReason,
        totalInternalRetries: result.totalInternalRetries,
        dimensionTrajectory: result.dimensionTrajectory,
        bestOutput: result.bestOutput,
        durationMs: result.durationMs,
        ...(result.rlmSessions ? { rlmSessions: result.rlmSessions } : {}),
      },
      null,
      2,
    ),
  };
}
