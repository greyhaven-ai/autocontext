import { ImprovementLoop } from "./improvement-loop.js";
import { SequentialDelegatedJudge, type JudgeInterface } from "../judge/delegated.js";
import { renderAgentTaskPrompt, resolveCustomAgentTask } from "../scenarios/custom-loader.js";
import type { LLMProvider, AgentTaskInterface, ImprovementResult } from "../types/index.js";
import { AgentTaskSpecSchema, type AgentTaskSpec } from "../scenarios/agent-task-spec.js";
import {
  NATIVE_AGENT_TASK_QUEUE_MARKER,
  requiresNativeAgentTaskExecution,
  savedAgentTaskSpecDigest,
} from "../scenarios/saved-agent-task-routing.js";
import type { TaskQueueRow } from "../storage/index.js";
import type { TaskConfig } from "./task-runner-config.js";
import { parseTaskConfig, serializeTaskResult } from "./task-runner-config.js";
import type { RlmSessionRecord, RlmTaskConfig } from "../rlm/types.js";
import type { QueuedTaskBrowserContextService } from "./queued-task-browser-context.js";
import type { TaskQueueWorkerStore } from "./task-queue-store.js";
import {
  createStructuredAgentTaskWorkflow,
  type StructuredWorkflowAgentTask,
} from "./structured-agent-task-workflow.js";

type SavedTaskSpec = Partial<AgentTaskSpec>;

interface SavedTaskLike {
  name?: string;
  spec: SavedTaskSpec;
}

export interface QueuedTaskExecutionPlan {
  taskPrompt: string;
  rubric: string;
  referenceContext?: string;
  browserUrl?: string;
  requiredConcepts?: string[];
  calibrationExamples?: Array<Record<string, unknown>>;
  maxRounds: number;
  qualityThreshold: number;
  minRounds: number;
  revisionPrompt?: string;
  initialOutput?: string;
  rlm?: RlmTaskConfig;
  delegatedJudge?: JudgeInterface;
  candidateGrounding?: boolean;
  nativeAgentTask?: {
    name: string;
    spec: AgentTaskSpec;
  };
}

interface WorkflowAgentTask extends AgentTaskInterface {
  generateOutput(context?: {
    referenceContext?: string;
    requiredConcepts?: string[];
    state?: Record<string, unknown>;
  }): Promise<string>;
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
    candidateGrounding: boolean;
  }): WorkflowAgentTask;
  createStructuredAgentTask(opts: {
    name: string;
    spec: AgentTaskSpec;
    provider: LLMProvider;
    model: string;
  }): StructuredWorkflowAgentTask;
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
  createStructuredAgentTask: createStructuredAgentTaskWorkflow,
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
  const queuedNativeTask = config.nativeTaskMarker === NATIVE_AGENT_TASK_QUEUE_MARKER;
  if (queuedNativeTask !== Boolean(config.savedSpecDigest)) {
    throw new Error(
      `Queued native task '${opts.task.spec_name}' has incomplete immutable saved-spec metadata`,
    );
  }
  if (queuedNativeTask && !opts.knowledgeRoot) {
    throw new Error(
      `Queued native task '${opts.task.spec_name}' requires a knowledge root to reload its saved spec`,
    );
  }
  const savedTask = opts.knowledgeRoot
    ? internals.resolveSavedTask(opts.knowledgeRoot, opts.task.spec_name)
    : null;
  if (queuedNativeTask && !savedTask) {
    throw new Error(`Queued native task '${opts.task.spec_name}' saved spec is missing`);
  }

  const savedTaskRequiresNative = Boolean(
    savedTask && requiresNativeAgentTaskExecution(savedTask.spec),
  );
  if (savedTaskRequiresNative && !queuedNativeTask) {
    throw new Error(
      `Queued native task '${opts.task.spec_name}' is missing immutable saved-spec metadata; enqueue it again`,
    );
  }
  if (queuedNativeTask && !savedTaskRequiresNative) {
    throw new Error(
      `Queued native task '${opts.task.spec_name}' no longer resolves to a native saved spec`,
    );
  }

  const nativeSavedTask =
    savedTaskRequiresNative && savedTask
      ? AgentTaskSpecSchema.safeParse(savedTask.spec)
      : null;
  if (nativeSavedTask && !nativeSavedTask.success) {
    throw new Error(
      `Saved structured task '${opts.task.spec_name}' cannot be executed because its normalized agent-task spec is invalid`,
    );
  }
  if (
    queuedNativeTask &&
    nativeSavedTask?.success &&
    savedAgentTaskSpecDigest(nativeSavedTask.data) !== config.savedSpecDigest
  ) {
    throw new Error(
      `Queued native task '${opts.task.spec_name}' saved spec digest does not match the immutable queued digest`,
    );
  }
  const savedRenderedPrompt = savedTask
    ? internals.renderSavedTaskPrompt(savedTask.spec)
    : undefined;
  const configuredTaskPrompt =
    nativeSavedTask?.success && config.taskPrompt === savedRenderedPrompt
      ? undefined
      : config.taskPrompt;

  const taskPrompt = configuredTaskPrompt
    ?? (nativeSavedTask?.success ? nativeSavedTask.data.taskPrompt : savedRenderedPrompt)
    ?? `Complete the task: ${opts.task.spec_name}`;
  const rubric = config.rubric
    ?? savedTask?.spec.judgeRubric
    ?? "Evaluate quality, accuracy, and completeness on a 0-1 scale.";

  const referenceContext = config.referenceContext ?? savedTask?.spec.referenceContext ?? undefined;
  const requiredConcepts = config.requiredConcepts ?? savedTask?.spec.requiredConcepts ?? undefined;
  const calibrationExamples =
    config.calibrationExamples ?? savedTask?.spec.calibrationExamples ?? undefined;
  const maxRounds = config.maxRounds ?? savedTask?.spec.maxRounds ?? 5;
  const qualityThreshold = config.qualityThreshold ?? savedTask?.spec.qualityThreshold ?? 0.9;
  const revisionPrompt = config.revisionPrompt ?? savedTask?.spec.revisionPrompt ?? undefined;
  const nativeAgentTask = nativeSavedTask?.success
    ? {
        name: savedTask?.name ?? opts.task.spec_name,
        spec: {
          ...nativeSavedTask.data,
          taskPrompt,
          judgeRubric: rubric,
          referenceContext,
          requiredConcepts,
          calibrationExamples,
          maxRounds,
          qualityThreshold,
          revisionPrompt,
        },
      }
    : undefined;
  const candidateGrounding = !savedTask || Boolean(nativeAgentTask);

  return {
    taskPrompt,
    rubric,
    referenceContext,
    browserUrl: config.browserUrl,
    requiredConcepts,
    calibrationExamples,
    maxRounds,
    qualityThreshold,
    minRounds: config.minRounds ?? 1,
    revisionPrompt,
    initialOutput: config.initialOutput,
    rlm: config.rlm,
    delegatedJudge: config.delegatedResults?.length
      ? new internals.createDelegatedJudge(config.delegatedResults, rubric)
      : undefined,
    candidateGrounding,
    ...(nativeAgentTask ? { nativeAgentTask } : {}),
  };
}

export async function executeQueuedTaskWorkflow(opts: {
  store: TaskQueueWorkerStore;
  maxAttempts?: number;
  task: TaskQueueRow;
  provider: LLMProvider;
  model: string;
  knowledgeRoot?: string;
  browserContextService?: QueuedTaskBrowserContextService;
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
    const resolvedReferenceContext = plan.browserUrl
      ? await resolveQueuedTaskBrowserReferenceContext({
          taskId: opts.task.id,
          browserUrl: plan.browserUrl,
          referenceContext: plan.referenceContext,
          browserContextService: opts.browserContextService,
        })
      : plan.referenceContext;

    if (plan.nativeAgentTask && plan.rlm) {
      throw new Error(
        "Queued structured tasks do not support RLM because it bypasses the native evaluator-isolated revision path",
      );
    }
    if (plan.nativeAgentTask && plan.delegatedJudge) {
      throw new Error(
        "Queued structured tasks do not support delegated judging because evaluator-only evidence requires native evaluation",
      );
    }
    const candidateGrounding = plan.candidateGrounding !== false;
    const agentTask = plan.nativeAgentTask
      ? internals.createStructuredAgentTask({
          name: plan.nativeAgentTask.name,
          spec: {
            ...plan.nativeAgentTask.spec,
            referenceContext: resolvedReferenceContext,
          },
          provider: opts.provider,
          model: opts.model,
        })
      : internals.createAgentTask({
          taskPrompt: plan.taskPrompt,
          rubric: plan.rubric,
          provider: opts.provider,
          model: opts.model,
          revisionPrompt: plan.revisionPrompt,
          rlm: plan.rlm,
          delegatedJudge: plan.delegatedJudge,
          candidateGrounding,
        });

    let initialState = agentTask.initialState();
    if (agentTask.prepareContext) {
      initialState = await agentTask.prepareContext(initialState);
    }
    const contextErrors = agentTask.validateContext?.(initialState) ?? [];
    if (contextErrors.length > 0) {
      throw new Error(`agent_task context preparation failed: ${contextErrors.join("; ")}`);
    }

    let initialOutput = plan.initialOutput;
    if (!initialOutput) {
      initialOutput = await agentTask.generateOutput({
        referenceContext: candidateGrounding ? resolvedReferenceContext : undefined,
        requiredConcepts: candidateGrounding ? plan.requiredConcepts : undefined,
        ...(plan.nativeAgentTask ? { state: initialState } : {}),
      });
    }

    const result = await internals.createImprovementLoop({
      task: agentTask,
      maxRounds: plan.maxRounds,
      qualityThreshold: plan.qualityThreshold,
      minRounds: plan.minRounds,
    }).run({
      initialOutput,
      state: initialState,
      referenceContext: resolvedReferenceContext,
      requiredConcepts: plan.requiredConcepts,
      calibrationExamples: plan.calibrationExamples,
    });

    if (plan.nativeAgentTask && !result.rounds.some((round) => round.judgeFailed === false)) {
      throw new Error(
        `Queued native task '${opts.task.spec_name}' produced no usable authoritative evaluation `
        + `(judge_failures=${result.judgeFailures}, total_rounds=${result.totalRounds}, `
        + `termination_reason=${result.terminationReason})`,
      );
    }

    await opts.store.completeTask(
      opts.task.id,
      result.bestScore,
      result.bestOutput,
      result.totalRounds,
      result.metThreshold,
      internals.serializeTaskResult(result, agentTask.getRlmSessions()),
    );
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    if (opts.maxAttempts === undefined) {
      await opts.store.failTask(opts.task.id, message);
    } else {
      await opts.store.failTask(opts.task.id, message, opts.maxAttempts);
    }
  }
}

async function resolveQueuedTaskBrowserReferenceContext(opts: {
  taskId: string;
  browserUrl: string;
  referenceContext?: string;
  browserContextService?: QueuedTaskBrowserContextService;
}): Promise<string> {
  if (!opts.browserContextService) {
    throw new Error("browser exploration is not configured");
  }
  return opts.browserContextService.buildReferenceContext({
    taskId: opts.taskId,
    browserUrl: opts.browserUrl,
    referenceContext: opts.referenceContext,
  });
}
