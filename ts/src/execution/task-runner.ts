/**
 * Task runner daemon for always-on evaluation.
 * Port of autocontext/src/autocontext/execution/task_runner.py
 */

import type {
  LLMProvider,
  AgentTaskInterface,
  AgentTaskResult,
} from "../types/index.js";
import type { AppSettings } from "../config/index.js";
import { type DelegatedResult, type JudgeInterface } from "../judge/delegated.js";
import type { SQLiteStore, TaskQueueRow } from "../storage/index.js";
import { assertFamilyContract } from "../scenarios/family-interfaces.js";
import {
  type RlmSessionRecord,
  type RlmTaskConfig,
} from "../rlm/types.js";
import {
  enqueueConfiguredTask,
  resolveRlmConfig,
  type EnqueueTaskRequest,
} from "./task-runner-config.js";
import type { TaskConfig } from "./task-runner-config.js";
import { executeQueuedTaskWorkflow } from "./task-processing-workflow.js";
import {
  createQueuedTaskBrowserContextService,
  type QueuedTaskBrowserContextService,
} from "./queued-task-browser-context.js";
import {
  evaluateSimpleAgentTaskOutput,
  generateSimpleAgentTaskOutput,
  reviseSimpleAgentTaskOutput,
} from "./simple-agent-task-workflow.js";
import {
  buildTaskRunnerModel,
  dequeueTaskBatch,
} from "./task-runner-loop-workflow.js";

export type { TaskConfig } from "./task-runner-config.js";

/**
 * A simple agent task built from queue config.
 */
export class SimpleAgentTask implements AgentTaskInterface {
  #taskPrompt: string;
  #rubric: string;
  #provider: LLMProvider;
  #model: string;
  #revisionPrompt?: string;
  readonly #rlmConfig: RlmTaskConfig | null;
  readonly #rlmSessions: RlmSessionRecord[] = [];
  #lastReferenceContext?: string;
  #lastRequiredConcepts?: string[];
  readonly #judgeOverride?: JudgeInterface;

  constructor(
    taskPrompt: string,
    rubric: string,
    provider: LLMProvider,
    model?: string,
    revisionPrompt?: string,
    rlmConfig?: Partial<RlmTaskConfig> | null,
    judgeOverride?: JudgeInterface,
  ) {
    this.#taskPrompt = taskPrompt;
    this.#rubric = rubric;
    this.#provider = provider;
    this.#model = model || provider.defaultModel();
    this.#revisionPrompt = revisionPrompt;
    this.#rlmConfig = resolveRlmConfig(rlmConfig);
    this.#judgeOverride = judgeOverride;
    assertFamilyContract(this, "agent_task", "SimpleAgentTask");
  }

  getTaskPrompt(): string {
    return this.#taskPrompt;
  }

  getRubric(): string {
    return this.#rubric;
  }

  initialState(): Record<string, unknown> {
    return {};
  }

  describeTask(): string {
    return this.#taskPrompt;
  }

  async evaluateOutput(
    output: string,
    _state: Record<string, unknown>,
    opts?: {
      referenceContext?: string;
      requiredConcepts?: string[];
      calibrationExamples?: Array<Record<string, unknown>>;
      pinnedDimensions?: string[];
    },
  ): Promise<AgentTaskResult> {
    this.#lastReferenceContext = opts?.referenceContext;
    this.#lastRequiredConcepts = opts?.requiredConcepts;
    return evaluateSimpleAgentTaskOutput({
      taskPrompt: this.#taskPrompt,
      rubric: this.#rubric,
      provider: this.#provider,
      model: this.#model,
      output,
      judgeOverride: this.#judgeOverride,
      referenceContext: opts?.referenceContext,
      requiredConcepts: opts?.requiredConcepts,
      calibrationExamples: opts?.calibrationExamples,
      pinnedDimensions: opts?.pinnedDimensions,
    });
  }

  getRlmSessions(): RlmSessionRecord[] {
    return this.#rlmSessions.slice();
  }

  async generateOutput(context?: {
    referenceContext?: string;
    requiredConcepts?: string[];
  }): Promise<string> {
    return generateSimpleAgentTaskOutput({
      provider: this.#provider,
      model: this.#model,
      taskPrompt: this.#taskPrompt,
      rubric: this.#rubric,
      rlmConfig: this.#rlmConfig,
      rlmSessions: this.#rlmSessions,
      referenceContext: context?.referenceContext,
      requiredConcepts: context?.requiredConcepts,
    });
  }

  async reviseOutput(
    output: string,
    judgeResult: AgentTaskResult,
    _state: Record<string, unknown>,
  ): Promise<string> {
    return reviseSimpleAgentTaskOutput({
      provider: this.#provider,
      model: this.#model,
      taskPrompt: this.#taskPrompt,
      rubric: this.#rubric,
      revisionPrompt: this.#revisionPrompt,
      output,
      judgeResult,
      rlmConfig: this.#rlmConfig,
      rlmSessions: this.#rlmSessions,
      referenceContext: this.#lastReferenceContext,
      requiredConcepts: this.#lastRequiredConcepts,
    });
  }
}

export interface TaskRunnerOpts {
  store: SQLiteStore;
  provider: LLMProvider;
  model?: string;
  knowledgeRoot?: string;
  browserContextService?: QueuedTaskBrowserContextService;
  pollInterval?: number;
  maxConsecutiveEmpty?: number;
  concurrency?: number;
}

export interface TaskRunnerFromSettingsOpts
  extends Omit<TaskRunnerOpts, "knowledgeRoot" | "browserContextService"> {
  settings: AppSettings;
  knowledgeRoot?: string;
  browserContextService?: QueuedTaskBrowserContextService;
  createBrowserContextService?: typeof createQueuedTaskBrowserContextService;
}

export class TaskRunner {
  #store: SQLiteStore;
  #provider: LLMProvider;
  #model: string;
  #knowledgeRoot?: string;
  #browserContextService?: QueuedTaskBrowserContextService;
  #pollInterval: number;
  #maxConsecutiveEmpty: number;
  #concurrency: number;
  #shutdown = false;
  #tasksProcessed = 0;

  constructor(opts: TaskRunnerOpts) {
    this.#store = opts.store;
    this.#provider = opts.provider;
    this.#model = buildTaskRunnerModel(opts.provider.defaultModel(), opts.model);
    this.#knowledgeRoot = opts.knowledgeRoot;
    this.#browserContextService = opts.browserContextService;
    this.#pollInterval = opts.pollInterval ?? 60;
    this.#maxConsecutiveEmpty = opts.maxConsecutiveEmpty ?? 0;
    this.#concurrency = Math.max(1, opts.concurrency ?? 1);
  }

  get tasksProcessed(): number {
    return this.#tasksProcessed;
  }

  async runOnce(): Promise<TaskQueueRow | null> {
    const task = this.#store.dequeueTask();
    if (!task) return null;
    await this.#processTask(task);
    this.#tasksProcessed++;
    return this.#store.getTask(task.id) ?? null;
  }

  async runBatch(limit?: number): Promise<number> {
    const maxTasks = limit ?? this.#concurrency;
    const tasks = dequeueTaskBatch(this.#store, maxTasks);
    if (tasks.length === 0) return 0;

    await Promise.all(tasks.map((task) => this.#processTask(task)));
    this.#tasksProcessed += tasks.length;
    return tasks.length;
  }

  shutdown(): void {
    this.#shutdown = true;
  }

  async #processTask(task: TaskQueueRow): Promise<void> {
    await executeQueuedTaskWorkflow({
      store: this.#store,
      task,
      provider: this.#provider,
      model: this.#model,
      knowledgeRoot: this.#knowledgeRoot,
      browserContextService: this.#browserContextService,
      internals: {
        createAgentTask: ({
          taskPrompt,
          rubric,
          provider,
          model,
          revisionPrompt,
          rlm,
          delegatedJudge,
        }) => new SimpleAgentTask(
          taskPrompt,
          rubric,
          provider,
          model,
          revisionPrompt,
          rlm,
          delegatedJudge,
        ),
      },
    });
  }
}

export function enqueueTask(
  store: SQLiteStore,
  specName: string,
  opts?: EnqueueTaskRequest,
): string {
  return enqueueConfiguredTask(store, specName, opts);
}

export function createTaskRunnerFromSettings(opts: TaskRunnerFromSettingsOpts): TaskRunner {
  const createBrowserContextService =
    opts.createBrowserContextService ?? createQueuedTaskBrowserContextService;
  const browserContextService = opts.browserContextService
    ?? (opts.settings.browserEnabled
      ? createBrowserContextService(opts.settings)
      : undefined);

  return new TaskRunner({
    store: opts.store,
    provider: opts.provider,
    model: opts.model,
    knowledgeRoot: opts.knowledgeRoot ?? opts.settings.knowledgeRoot,
    browserContextService,
    pollInterval: opts.pollInterval,
    maxConsecutiveEmpty: opts.maxConsecutiveEmpty,
    concurrency: opts.concurrency,
  });
}
