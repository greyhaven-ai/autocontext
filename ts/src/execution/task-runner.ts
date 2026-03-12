/**
 * Task runner daemon for always-on evaluation.
 * Port of autocontext/src/autocontext/execution/task_runner.py
 */

import { randomUUID } from "node:crypto";
import { z } from "zod";
import type {
  LLMProvider,
  AgentTaskInterface,
  AgentTaskResult,
  ImprovementResult,
} from "../types/index.js";
import { ImprovementLoop } from "./improvement-loop.js";
import { LLMJudge } from "../judge/index.js";
import type { SQLiteStore, TaskQueueRow } from "../storage/index.js";

export interface TaskConfig {
  maxRounds: number;
  qualityThreshold: number;
  minRounds: number;
  referenceContext?: string;
  requiredConcepts?: string[];
  calibrationExamples?: Array<Record<string, unknown>>;
  initialOutput?: string;
  rubric?: string;
  taskPrompt?: string;
  revisionPrompt?: string;
}

const TaskConfigSchema = z.object({
  max_rounds: z.number().int().positive().optional(),
  quality_threshold: z.number().min(0).max(1).optional(),
  min_rounds: z.number().int().positive().optional(),
  reference_context: z.string().optional(),
  required_concepts: z.array(z.string()).optional(),
  calibration_examples: z.array(z.record(z.unknown())).optional(),
  initial_output: z.string().optional(),
  rubric: z.string().optional(),
  task_prompt: z.string().optional(),
  revision_prompt: z.string().optional(),
}).passthrough();

function parseTaskConfig(json: string | null): TaskConfig {
  if (!json) return { maxRounds: 5, qualityThreshold: 0.9, minRounds: 1 };
  const raw = JSON.parse(json);
  const d = TaskConfigSchema.parse(raw);
  return {
    maxRounds: d.max_rounds ?? 5,
    qualityThreshold: d.quality_threshold ?? 0.9,
    minRounds: d.min_rounds ?? 1,
    referenceContext: d.reference_context,
    requiredConcepts: d.required_concepts,
    calibrationExamples: d.calibration_examples,
    initialOutput: d.initial_output,
    rubric: d.rubric,
    taskPrompt: d.task_prompt,
    revisionPrompt: d.revision_prompt,
  };
}

function serializeResult(result: ImprovementResult): string {
  return JSON.stringify({
    rounds: result.rounds.map((r) => ({
      round_number: r.roundNumber,
      score: r.score,
      reasoning: r.reasoning,
      dimension_scores: r.dimensionScores,
      is_revision: r.isRevision,
    })),
    best_score: result.bestScore,
    best_round: result.bestRound,
    total_rounds: result.totalRounds,
    met_threshold: result.metThreshold,
    ...(result.durationMs != null ? { duration_ms: result.durationMs } : {}),
    ...(result.judgeCalls ? { judge_calls: result.judgeCalls } : {}),
  });
}

/**
 * A simple agent task built from queue config.
 */
export class SimpleAgentTask implements AgentTaskInterface {
  constructor(
    private taskPrompt: string,
    private rubric: string,
    private provider: LLMProvider,
    private model: string = "claude-sonnet-4-20250514",
    private revisionPrompt?: string,
  ) {}

  getTaskPrompt(): string {
    return this.taskPrompt;
  }

  getRubric(): string {
    return this.rubric;
  }

  initialState(): Record<string, unknown> {
    return {};
  }

  describeTask(): string {
    return this.taskPrompt;
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
    const judge = new LLMJudge({
      provider: this.provider,
      model: this.model,
      rubric: this.rubric,
    });
    const result = await judge.evaluate({
      taskPrompt: this.taskPrompt,
      agentOutput: output,
      referenceContext: opts?.referenceContext,
      requiredConcepts: opts?.requiredConcepts,
      calibrationExamples: opts?.calibrationExamples,
      pinnedDimensions: opts?.pinnedDimensions,
    });
    return {
      score: result.score,
      reasoning: result.reasoning,
      dimensionScores: result.dimensionScores,
      internalRetries: result.internalRetries ?? 0,
    };
  }

  async generateOutput(): Promise<string> {
    const result = await this.provider.complete({
      systemPrompt:
        "You are a skilled writer and analyst. Complete the task precisely.",
      userPrompt: this.taskPrompt,
      model: this.model,
    });
    return result.text;
  }

  async reviseOutput(
    output: string,
    judgeResult: AgentTaskResult,
    _state: Record<string, unknown>,
  ): Promise<string> {
    const instruction =
      this.revisionPrompt ??
      "Revise the following output based on the judge's feedback. Maintain what works, fix what doesn't.";

    const prompt =
      `${instruction}\n\n` +
      `## Original Output\n${output}\n\n` +
      `## Judge Score: ${judgeResult.score.toFixed(2)}\n` +
      `## Judge Feedback\n${judgeResult.reasoning}\n\n` +
      `## Task\n${this.taskPrompt}\n\n` +
      "Produce an improved version:";

    const result = await this.provider.complete({
      systemPrompt:
        "You are revising content based on expert feedback. Improve the output. " +
        "IMPORTANT: Return ONLY the revised content. Do NOT include analysis, " +
        "explanations, headers like '## Revised Output', or self-assessment. " +
        "Just output the improved version directly.",
      userPrompt: prompt,
      model: this.model,
    });
    return result.text;
  }
}

export interface TaskRunnerOpts {
  store: SQLiteStore;
  provider: LLMProvider;
  model?: string;
  pollInterval?: number;
  maxConsecutiveEmpty?: number;
  concurrency?: number;
}

export class TaskRunner {
  private store: SQLiteStore;
  private provider: LLMProvider;
  private model: string;
  private pollInterval: number;
  private maxConsecutiveEmpty: number;
  private concurrency: number;
  private _shutdown = false;
  private _tasksProcessed = 0;

  constructor(opts: TaskRunnerOpts) {
    this.store = opts.store;
    this.provider = opts.provider;
    this.model = opts.model ?? "claude-sonnet-4-20250514";
    this.pollInterval = opts.pollInterval ?? 60;
    this.maxConsecutiveEmpty = opts.maxConsecutiveEmpty ?? 0;
    this.concurrency = Math.max(1, opts.concurrency ?? 1);
  }

  get tasksProcessed(): number {
    return this._tasksProcessed;
  }

  async runOnce(): Promise<TaskQueueRow | null> {
    const task = this.store.dequeueTask();
    if (!task) return null;
    await this.processTask(task);
    this._tasksProcessed++;
    return this.store.getTask(task.id) ?? null;
  }

  async runBatch(limit?: number): Promise<number> {
    const maxTasks = limit ?? this.concurrency;
    const tasks: TaskQueueRow[] = [];
    for (let i = 0; i < maxTasks; i++) {
      const task = this.store.dequeueTask();
      if (!task) break;
      tasks.push(task);
    }
    if (tasks.length === 0) return 0;

    await Promise.all(tasks.map((task) => this.processTask(task)));
    this._tasksProcessed += tasks.length;
    return tasks.length;
  }

  shutdown(): void {
    this._shutdown = true;
  }

  private async processTask(task: TaskQueueRow): Promise<void> {
    try {
      const config = parseTaskConfig(task.config_json);

      const agentTask = new SimpleAgentTask(
        config.taskPrompt ?? `Complete the task: ${task.spec_name}`,
        config.rubric ?? "Evaluate quality, accuracy, and completeness on a 0-1 scale.",
        this.provider,
        this.model,
        config.revisionPrompt,
      );

      let initialOutput = config.initialOutput;
      if (!initialOutput) {
        initialOutput = await agentTask.generateOutput();
      }

      const loop = new ImprovementLoop({
        task: agentTask,
        maxRounds: config.maxRounds,
        qualityThreshold: config.qualityThreshold,
        minRounds: config.minRounds,
      });

      const result = await loop.run({
        initialOutput,
        state: {},
        referenceContext: config.referenceContext,
        requiredConcepts: config.requiredConcepts,
        calibrationExamples: config.calibrationExamples,
      });

      this.store.completeTask(
        task.id,
        result.bestScore,
        result.bestOutput,
        result.totalRounds,
        result.metThreshold,
        serializeResult(result),
      );
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      this.store.failTask(task.id, msg);
    }
  }
}

export function enqueueTask(
  store: SQLiteStore,
  specName: string,
  opts?: {
    taskPrompt?: string;
    rubric?: string;
    referenceContext?: string;
    requiredConcepts?: string[];
    maxRounds?: number;
    qualityThreshold?: number;
    minRounds?: number;
    initialOutput?: string;
    priority?: number;
  },
): string {
  const taskId = randomUUID();
  const config: Record<string, unknown> = {};
  if (opts?.maxRounds != null) config.max_rounds = opts.maxRounds;
  if (opts?.qualityThreshold != null) config.quality_threshold = opts.qualityThreshold;
  if (opts?.minRounds != null) config.min_rounds = opts.minRounds;
  if (opts?.taskPrompt) config.task_prompt = opts.taskPrompt;
  if (opts?.rubric) config.rubric = opts.rubric;
  if (opts?.referenceContext) config.reference_context = opts.referenceContext;
  if (opts?.requiredConcepts) config.required_concepts = opts.requiredConcepts;
  if (opts?.initialOutput) config.initial_output = opts.initialOutput;

  store.enqueueTask(
    taskId,
    specName,
    opts?.priority ?? 0,
    Object.keys(config).length > 0 ? config : undefined,
  );
  return taskId;
}
