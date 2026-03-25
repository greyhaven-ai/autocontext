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
import { renderAgentTaskPrompt, resolveCustomAgentTask } from "../scenarios/custom-loader.js";
import {
  RlmTaskConfigSchema,
  type RlmSessionRecord,
  type RlmTaskConfig,
} from "../rlm/index.js";
import { runAgentTaskRlmSession } from "../rlm/index.js";

export interface TaskConfig {
  maxRounds?: number;
  qualityThreshold?: number;
  minRounds?: number;
  referenceContext?: string;
  requiredConcepts?: string[];
  calibrationExamples?: Array<Record<string, unknown>>;
  initialOutput?: string;
  rubric?: string;
  taskPrompt?: string;
  revisionPrompt?: string;
  rlm?: RlmTaskConfig;
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
  rlm_enabled: z.boolean().optional(),
  rlm_model: z.string().optional(),
  rlm_max_turns: z.number().int().positive().optional(),
  rlm_max_tokens_per_turn: z.number().int().positive().optional(),
  rlm_temperature: z.number().min(0).max(2).optional(),
  rlm_max_stdout_chars: z.number().int().positive().optional(),
  rlm_code_timeout_ms: z.number().int().positive().optional(),
  rlm_memory_limit_mb: z.number().int().positive().optional(),
}).passthrough();

function resolveRlmConfig(raw: Partial<RlmTaskConfig> | null | undefined): RlmTaskConfig | null {
  if (!raw?.enabled) return null;
  return RlmTaskConfigSchema.parse(raw);
}

function parseTaskConfig(json: string | null): TaskConfig {
  if (!json) return {};
  const raw = JSON.parse(json);
  const d = TaskConfigSchema.parse(raw);
  return {
    maxRounds: d.max_rounds,
    qualityThreshold: d.quality_threshold,
    minRounds: d.min_rounds,
    referenceContext: d.reference_context,
    requiredConcepts: d.required_concepts,
    calibrationExamples: d.calibration_examples,
    initialOutput: d.initial_output,
    rubric: d.rubric,
    taskPrompt: d.task_prompt,
    revisionPrompt: d.revision_prompt,
    rlm: resolveRlmConfig({
      enabled: d.rlm_enabled ?? false,
      model: d.rlm_model,
      maxTurns: d.rlm_max_turns,
      maxTokensPerTurn: d.rlm_max_tokens_per_turn,
      temperature: d.rlm_temperature,
      maxStdoutChars: d.rlm_max_stdout_chars,
      codeTimeoutMs: d.rlm_code_timeout_ms,
      memoryLimitMb: d.rlm_memory_limit_mb,
    }) ?? undefined,
  };
}

function serializeResult(
  result: ImprovementResult,
  rlmSessions?: RlmSessionRecord[],
): string {
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
    ...(rlmSessions && rlmSessions.length > 0 ? { rlm_sessions: rlmSessions } : {}),
  });
}

/**
 * A simple agent task built from queue config.
 */
export class SimpleAgentTask implements AgentTaskInterface {
  private taskPrompt: string;
  private rubric: string;
  private provider: LLMProvider;
  private model: string;
  private revisionPrompt?: string;
  private readonly rlmConfig: RlmTaskConfig | null;
  private readonly rlmSessions: RlmSessionRecord[] = [];
  private lastReferenceContext?: string;
  private lastRequiredConcepts?: string[];

  constructor(
    taskPrompt: string,
    rubric: string,
    provider: LLMProvider,
    model?: string,
    revisionPrompt?: string,
    rlmConfig?: Partial<RlmTaskConfig> | null,
  ) {
    this.taskPrompt = taskPrompt;
    this.rubric = rubric;
    this.provider = provider;
    this.model = model || provider.defaultModel();
    this.revisionPrompt = revisionPrompt;
    this.rlmConfig = resolveRlmConfig(rlmConfig);
  }

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
    this.lastReferenceContext = opts?.referenceContext;
    this.lastRequiredConcepts = opts?.requiredConcepts;
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

  getRlmSessions(): RlmSessionRecord[] {
    return this.rlmSessions.slice();
  }

  private async runRlm(phase: "generate" | "revise", opts: {
    currentOutput?: string;
    judgeResult?: AgentTaskResult;
    referenceContext?: string;
    requiredConcepts?: string[];
  }): Promise<string | null> {
    if (!this.rlmConfig) return null;
    const record = await runAgentTaskRlmSession({
      provider: this.provider,
      model: this.model,
      config: this.rlmConfig,
      phase,
      taskPrompt: this.taskPrompt,
      rubric: this.rubric,
      currentOutput: opts.currentOutput,
      judgeResult: opts.judgeResult,
      referenceContext: opts.referenceContext,
      requiredConcepts: opts.requiredConcepts,
      revisionPrompt: this.revisionPrompt,
    });
    this.rlmSessions.push(record);
    const content = record.content.trim();
    return content.length > 0 ? content : null;
  }

  async generateOutput(context?: {
    referenceContext?: string;
    requiredConcepts?: string[];
  }): Promise<string> {
    const rlmOutput = await this.runRlm("generate", {
      referenceContext: context?.referenceContext,
      requiredConcepts: context?.requiredConcepts,
    });
    if (rlmOutput) return rlmOutput;

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
    const rlmOutput = await this.runRlm("revise", {
      currentOutput: output,
      judgeResult,
      referenceContext: this.lastReferenceContext,
      requiredConcepts: this.lastRequiredConcepts,
    });
    if (rlmOutput) return rlmOutput;

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
  knowledgeRoot?: string;
  pollInterval?: number;
  maxConsecutiveEmpty?: number;
  concurrency?: number;
}

export class TaskRunner {
  private store: SQLiteStore;
  private provider: LLMProvider;
  private model: string;
  private knowledgeRoot?: string;
  private pollInterval: number;
  private maxConsecutiveEmpty: number;
  private concurrency: number;
  private _shutdown = false;
  private _tasksProcessed = 0;

  constructor(opts: TaskRunnerOpts) {
    this.store = opts.store;
    this.provider = opts.provider;
    this.model = opts.model || opts.provider.defaultModel();
    this.knowledgeRoot = opts.knowledgeRoot;
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
      const savedTask = this.knowledgeRoot
        ? resolveCustomAgentTask(this.knowledgeRoot, task.spec_name)
        : null;
      const taskPrompt = config.taskPrompt
        ?? (savedTask ? renderAgentTaskPrompt(savedTask.spec) : undefined)
        ?? `Complete the task: ${task.spec_name}`;
      const rubric = config.rubric
        ?? savedTask?.spec.judgeRubric
        ?? "Evaluate quality, accuracy, and completeness on a 0-1 scale.";
      const referenceContext = config.referenceContext ?? savedTask?.spec.referenceContext ?? undefined;
      const requiredConcepts = config.requiredConcepts ?? savedTask?.spec.requiredConcepts ?? undefined;
      const calibrationExamples = config.calibrationExamples ?? savedTask?.spec.calibrationExamples ?? undefined;
      const maxRounds = config.maxRounds ?? savedTask?.spec.maxRounds ?? 5;
      const qualityThreshold = config.qualityThreshold ?? savedTask?.spec.qualityThreshold ?? 0.9;
      const minRounds = config.minRounds ?? 1;
      const revisionPrompt = config.revisionPrompt ?? savedTask?.spec.revisionPrompt ?? undefined;

      const agentTask = new SimpleAgentTask(
        taskPrompt,
        rubric,
        this.provider,
        this.model,
        revisionPrompt,
        config.rlm,
      );

      let initialOutput = config.initialOutput;
      if (!initialOutput) {
        initialOutput = await agentTask.generateOutput({
          referenceContext,
          requiredConcepts,
        });
      }

      const loop = new ImprovementLoop({
        task: agentTask,
        maxRounds,
        qualityThreshold,
        minRounds,
      });

      const result = await loop.run({
        initialOutput,
        state: agentTask.initialState(),
        referenceContext,
        requiredConcepts,
        calibrationExamples,
      });

      this.store.completeTask(
        task.id,
        result.bestScore,
        result.bestOutput,
        result.totalRounds,
        result.metThreshold,
        serializeResult(result, agentTask.getRlmSessions()),
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
    rlmEnabled?: boolean;
    rlmModel?: string;
    rlmMaxTurns?: number;
    rlmMaxTokensPerTurn?: number;
    rlmTemperature?: number;
    rlmMaxStdoutChars?: number;
    rlmCodeTimeoutMs?: number;
    rlmMemoryLimitMb?: number;
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
  if (opts?.rlmEnabled != null) config.rlm_enabled = opts.rlmEnabled;
  if (opts?.rlmModel) config.rlm_model = opts.rlmModel;
  if (opts?.rlmMaxTurns != null) config.rlm_max_turns = opts.rlmMaxTurns;
  if (opts?.rlmMaxTokensPerTurn != null) config.rlm_max_tokens_per_turn = opts.rlmMaxTokensPerTurn;
  if (opts?.rlmTemperature != null) config.rlm_temperature = opts.rlmTemperature;
  if (opts?.rlmMaxStdoutChars != null) config.rlm_max_stdout_chars = opts.rlmMaxStdoutChars;
  if (opts?.rlmCodeTimeoutMs != null) config.rlm_code_timeout_ms = opts.rlmCodeTimeoutMs;
  if (opts?.rlmMemoryLimitMb != null) config.rlm_memory_limit_mb = opts.rlmMemoryLimitMb;

  store.enqueueTask(
    taskId,
    specName,
    opts?.priority ?? 0,
    Object.keys(config).length > 0 ? config : undefined,
  );
  return taskId;
}
