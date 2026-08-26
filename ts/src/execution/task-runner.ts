/**
 * Task runner daemon for always-on evaluation.
 * Port of autocontext/src/autocontext/execution/task_runner.py
 */

import type {
  LLMProvider,
  AgentTaskInterface,
  AgentTaskResult,
} from "../types/index.js";
import type { HookBus } from "../extensions/index.js";
import type { AppSettings } from "../config/index.js";
import { type DelegatedResult, type JudgeInterface } from "../judge/delegated.js";
import type { TaskQueueRow } from "../storage/index.js";
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
import { createStructuredAgentTaskWorkflow } from "./structured-agent-task-workflow.js";
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
import type {
  TaskQueueEnqueueStore,
  TaskQueueWorkerStore,
} from "./task-queue-store.js";

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
  readonly #hookBus: HookBus | null;
  readonly #candidateGrounding: boolean;

  constructor(
    taskPrompt: string,
    rubric: string,
    provider: LLMProvider,
    model?: string,
    revisionPrompt?: string,
    rlmConfig?: Partial<RlmTaskConfig> | null,
    judgeOverride?: JudgeInterface,
    hookBus?: HookBus | null,
    candidateGrounding = true,
  ) {
    this.#taskPrompt = taskPrompt;
    this.#rubric = rubric;
    this.#provider = provider;
    this.#model = model || provider.defaultModel();
    this.#revisionPrompt = revisionPrompt;
    this.#rlmConfig = resolveRlmConfig(rlmConfig);
    this.#judgeOverride = judgeOverride;
    this.#hookBus = hookBus ?? null;
    this.#candidateGrounding = candidateGrounding;
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
      hookBus: this.#hookBus,
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
      rubric: this.#candidateGrounding ? this.#rubric : "",
      rlmConfig: this.#rlmConfig,
      rlmSessions: this.#rlmSessions,
      hookBus: this.#hookBus,
      referenceContext: this.#candidateGrounding ? context?.referenceContext : undefined,
      requiredConcepts: this.#candidateGrounding ? context?.requiredConcepts : undefined,
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
      rubric: this.#candidateGrounding ? this.#rubric : "",
      revisionPrompt: this.#revisionPrompt,
      output,
      judgeResult,
      rlmConfig: this.#rlmConfig,
      rlmSessions: this.#rlmSessions,
      hookBus: this.#hookBus,
      referenceContext: this.#candidateGrounding ? this.#lastReferenceContext : undefined,
      requiredConcepts: this.#candidateGrounding ? this.#lastRequiredConcepts : undefined,
      candidateGrounding: this.#candidateGrounding,
    });
  }
}

export interface TaskRunnerOpts {
  store: TaskQueueWorkerStore;
  provider: LLMProvider;
  model?: string;
  knowledgeRoot?: string;
  browserContextService?: QueuedTaskBrowserContextService;
  pollInterval?: number;
  maxConsecutiveEmpty?: number;
  /** AC-906: failed claims retry up to this many attempts before dead-lettering. */
  maxAttempts?: number;
  /** AC-906: running rows older than this are recovered to pending at startup. */
  staleRunningAfterS?: number;
  concurrency?: number;
  hookBus?: HookBus | null;
}

export interface TaskRunnerFromSettingsOpts
  extends Omit<TaskRunnerOpts, "knowledgeRoot" | "browserContextService"> {
  settings: AppSettings;
  knowledgeRoot?: string;
  browserContextService?: QueuedTaskBrowserContextService;
  createBrowserContextService?: typeof createQueuedTaskBrowserContextService;
}

export class TaskRunner {
  #store: TaskQueueWorkerStore;
  #provider: LLMProvider;
  #model: string;
  #knowledgeRoot?: string;
  #browserContextService?: QueuedTaskBrowserContextService;
  #pollInterval: number;
  #maxConsecutiveEmpty: number;
  #maxAttempts: number;
  #staleRunningAfterS: number;
  #concurrency: number;
  #hookBus: HookBus | null;
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
    this.#maxAttempts = Math.max(1, opts.maxAttempts ?? 3);
    this.#staleRunningAfterS = opts.staleRunningAfterS ?? 3600;
    this.#concurrency = Math.max(1, opts.concurrency ?? 1);
    this.#hookBus = opts.hookBus ?? null;
  }

  get tasksProcessed(): number {
    return this.#tasksProcessed;
  }

  async runOnce(): Promise<TaskQueueRow | null> {
    const task = await this.#store.dequeueTask();
    if (!task) return null;
    await this.#processTask(task);
    this.#tasksProcessed++;
    return (await this.#store.getTask(task.id)) ?? null;
  }

  async runBatch(limit?: number): Promise<number> {
    const maxTasks = limit ?? this.#concurrency;
    const tasks = await dequeueTaskBatch(this.#store, maxTasks);
    if (tasks.length === 0) return 0;

    // AC-906: allSettled so one rejected task (e.g. a throw from failTask
    // itself) cannot tear down the batch and strand siblings in running.
    // Return the CLAIMED count so an all-rejected batch does not read as an
    // empty poll and exit the daemon early; only fulfilled tasks count as
    // processed.
    const outcomes = await Promise.allSettled(tasks.map((task) => this.#processTask(task)));
    for (const [index, outcome] of outcomes.entries()) {
      if (outcome.status === "rejected") {
        console.error(`task ${tasks[index].id} processing rejected:`, outcome.reason);
      }
    }
    this.#tasksProcessed += outcomes.filter((outcome) => outcome.status === "fulfilled").length;
    return tasks.length;
  }

  async run(): Promise<number> {
    // AC-906: recover tasks stranded in running by a previous crash.
    const recovered = await this.#store.requeueStaleRunning?.(this.#staleRunningAfterS, this.#maxAttempts);
    if (recovered) {
      console.warn(`recovered ${recovered} crash-stranded running task(s) to pending`);
    }
    let consecutiveEmpty = 0;

    while (!this.#shutdown) {
      const processed = await this.runBatch(this.#concurrency);
      if (processed === 0) {
        consecutiveEmpty++;
        if (
          this.#maxConsecutiveEmpty > 0 &&
          consecutiveEmpty >= this.#maxConsecutiveEmpty
        ) {
          break;
        }
        await this.#sleep(this.#pollInterval);
        continue;
      }

      consecutiveEmpty = 0;
    }

    return this.#tasksProcessed;
  }

  shutdown(): void {
    this.#shutdown = true;
  }

  async #processTask(task: TaskQueueRow): Promise<void> {
    await executeQueuedTaskWorkflow({
      store: this.#store,
      task,
      maxAttempts: this.#maxAttempts,
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
          candidateGrounding,
        }) => new SimpleAgentTask(
          taskPrompt,
          rubric,
          provider,
          model,
          revisionPrompt,
          rlm,
          delegatedJudge,
          this.#hookBus,
          candidateGrounding,
        ),
        createStructuredAgentTask: (taskOpts) =>
          createStructuredAgentTaskWorkflow({
            ...taskOpts,
            hookBus: this.#hookBus,
          }),
      },
    });
  }

  async #sleep(seconds: number): Promise<void> {
    let remainingMs = Math.max(0, seconds * 1000);
    while (remainingMs > 0 && !this.#shutdown) {
      const chunkMs = Math.min(1000, remainingMs);
      await new Promise((resolve) => setTimeout(resolve, chunkMs));
      remainingMs -= chunkMs;
    }
  }
}

export function enqueueTask(
  store: TaskQueueEnqueueStore,
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
    hookBus: opts.hookBus,
  });
}
