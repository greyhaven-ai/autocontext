import { ImprovementLoop } from "./improvement-loop.js";
import { SequentialDelegatedJudge, type JudgeInterface } from "../judge/delegated.js";
import { renderAgentTaskPrompt, resolveCustomAgentTask } from "../scenarios/custom-loader.js";
import type { LLMProvider, AgentTaskInterface, ImprovementResult } from "../types/index.js";
import type { SQLiteStore, TaskQueueRow } from "../storage/index.js";
import type { TaskConfig } from "./task-runner-config.js";
import { parseTaskConfig, serializeTaskResult } from "./task-runner-config.js";
import type { RlmSessionRecord, RlmTaskConfig } from "../rlm/types.js";

interface SavedTaskSpec {
  judgeRubric?: string;
  referenceContext?: string;
  requiredConcepts?: string[];
  calibrationExamples?: Array<Record<string, unknown>>;
  maxRounds?: number;
  qualityThreshold?: number;
  revisionPrompt?: string;
}

interface SavedTaskLike {
  spec: SavedTaskSpec;
}

export interface QueuedTaskExecutionPlan {
  taskPrompt: string;
  rubric: string;
  referenceContext?: string;
  requiredConcepts?: string[];
  calibrationExamples?: Array<Record<string, unknown>>;
  maxRounds: number;
  qualityThreshold: number;
  minRounds: number;
  revisionPrompt?: string;
  initialOutput?: string;
  rlm?: RlmTaskConfig;
  delegatedJudge?: JudgeInterface;
}

interface WorkflowAgentTask extends AgentTaskInterface {
  generateOutput(context?: { referenceContext?: string; requiredConcepts?: string[] }): Promise<string>;
  getRlmSessions(): RlmSessionRecord[];
}

interface ImprovementLoopLike {
  run(opts: {
    initialOutput: string;
    state: Record<string, unknown>;
    referenceContext?: string;
    requiredConcepts?: string[];
    calibrationExamples?: Array<Record<string, unknown>>;
  }): Promise<ImprovementResult>;
}

interface TaskProcessingInternals {
  parseTaskConfig: typeof parseTaskConfig;
  resolveSavedTask(knowledgeRoot: string, specName: string): SavedTaskLike | null;
  renderSavedTaskPrompt(spec: SavedTaskSpec): string;
  createDelegatedJudge: typeof SequentialDelegatedJudge;
  createAgentTask(opts: {
    taskPrompt: string;
    rubric: string;
    provider: LLMProvider;
    model: string;
    revisionPrompt?: string;
    rlm?: RlmTaskConfig;
    delegatedJudge?: JudgeInterface;
  }): WorkflowAgentTask;
  createImprovementLoop(opts: {
    task: WorkflowAgentTask;
    maxRounds: number;
    qualityThreshold: number;
    minRounds: number;
  }): ImprovementLoopLike;
  serializeTaskResult: typeof serializeTaskResult;
}

const defaultInternals: TaskProcessingInternals = {
  parseTaskConfig,
  resolveSavedTask: (knowledgeRoot, specName) =>
    resolveCustomAgentTask(knowledgeRoot, specName) as unknown as SavedTaskLike | null,
  renderSavedTaskPrompt: (spec) => renderAgentTaskPrompt(spec as Parameters<typeof renderAgentTaskPrompt>[0]),
  createDelegatedJudge: SequentialDelegatedJudge,
  createAgentTask: () => {
    throw new Error("createAgentTask must be provided");
  },
  createImprovementLoop: (opts) => new ImprovementLoop(opts),
  serializeTaskResult,
};

export function buildQueuedTaskExecutionPlan(opts: {
  task: Pick<TaskQueueRow, "spec_name" | "config_json">;
  knowledgeRoot?: string;
  internals?: Partial<TaskProcessingInternals>;
}): QueuedTaskExecutionPlan {
  const internals: TaskProcessingInternals = {
    ...defaultInternals,
    ...opts.internals,
  };
  const config = internals.parseTaskConfig(opts.task.config_json);
  const savedTask = opts.knowledgeRoot
    ? internals.resolveSavedTask(opts.knowledgeRoot, opts.task.spec_name)
    : null;

  const taskPrompt = config.taskPrompt
    ?? (savedTask ? internals.renderSavedTaskPrompt(savedTask.spec) : undefined)
    ?? `Complete the task: ${opts.task.spec_name}`;
  const rubric = config.rubric
    ?? savedTask?.spec.judgeRubric
    ?? "Evaluate quality, accuracy, and completeness on a 0-1 scale.";

  return {
    taskPrompt,
    rubric,
    referenceContext: config.referenceContext ?? savedTask?.spec.referenceContext ?? undefined,
    requiredConcepts: config.requiredConcepts ?? savedTask?.spec.requiredConcepts ?? undefined,
    calibrationExamples: config.calibrationExamples ?? savedTask?.spec.calibrationExamples ?? undefined,
    maxRounds: config.maxRounds ?? savedTask?.spec.maxRounds ?? 5,
    qualityThreshold: config.qualityThreshold ?? savedTask?.spec.qualityThreshold ?? 0.9,
    minRounds: config.minRounds ?? 1,
    revisionPrompt: config.revisionPrompt ?? savedTask?.spec.revisionPrompt ?? undefined,
    initialOutput: config.initialOutput,
    rlm: config.rlm,
    delegatedJudge: config.delegatedResults?.length
      ? new internals.createDelegatedJudge(config.delegatedResults, rubric)
      : undefined,
  };
}

export async function executeQueuedTaskWorkflow(opts: {
  store: SQLiteStore;
  task: TaskQueueRow;
  provider: LLMProvider;
  model: string;
  knowledgeRoot?: string;
  internals?: Partial<TaskProcessingInternals>;
}): Promise<void> {
  const internals: TaskProcessingInternals = {
    ...defaultInternals,
    ...opts.internals,
  };

  try {
    const plan = buildQueuedTaskExecutionPlan({
      task: opts.task,
      knowledgeRoot: opts.knowledgeRoot,
      internals,
    });

    const agentTask = internals.createAgentTask({
      taskPrompt: plan.taskPrompt,
      rubric: plan.rubric,
      provider: opts.provider,
      model: opts.model,
      revisionPrompt: plan.revisionPrompt,
      rlm: plan.rlm,
      delegatedJudge: plan.delegatedJudge,
    });

    let initialOutput = plan.initialOutput;
    if (!initialOutput) {
      initialOutput = await agentTask.generateOutput({
        referenceContext: plan.referenceContext,
        requiredConcepts: plan.requiredConcepts,
      });
    }

    const result = await internals.createImprovementLoop({
      task: agentTask,
      maxRounds: plan.maxRounds,
      qualityThreshold: plan.qualityThreshold,
      minRounds: plan.minRounds,
    }).run({
      initialOutput,
      state: agentTask.initialState(),
      referenceContext: plan.referenceContext,
      requiredConcepts: plan.requiredConcepts,
      calibrationExamples: plan.calibrationExamples,
    });

    opts.store.completeTask(
      opts.task.id,
      result.bestScore,
      result.bestOutput,
      result.totalRounds,
      result.metThreshold,
      internals.serializeTaskResult(result, agentTask.getRlmSessions()),
    );
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    opts.store.failTask(opts.task.id, message);
  }
}
