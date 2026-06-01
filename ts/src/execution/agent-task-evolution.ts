/**
 * Multi-generation support for AgentTask scenarios.
 *
 * TypeScript port of `autocontext/execution/agent_task_evolution.py` (AC-281),
 * maintained at behavioral parity with the Python module. The framework's
 * native multi-generation loop for agent tasks: lesson accumulation,
 * best-tracking, and enriched prompts carried across generations.
 */

import { compactPromptComponent } from "../knowledge/semantic-compaction.js";
import type { AgentTaskResult } from "../types/index.js";

/** Cross-generation state for an agent task evolution run. */
export interface AgentTaskGenerationState {
  generation: number;
  bestOutput: string;
  bestScore: number;
  playbook: string;
  scoreHistory: number[];
  lessonHistory: string[];
  metadata: Record<string, unknown>;
}

/**
 * Structured, evaluator-provided guidance for the next generation.
 *
 * Domain-aware lesson accumulation: a deterministic evaluator usually knows
 * why a candidate plateaued and what to try next. Carrying that here lets
 * `accumulateLessons` write actionable playbook entries instead of only
 * score + dimension scores.
 */
export interface LessonSignal {
  hint?: string;
  plateau?: boolean;
  metrics?: Record<string, number>;
}

/** Evaluation result for one cross-generation candidate. */
export interface AgentTaskGenerationEvaluation {
  output: string;
  score: number;
  reasoning: string;
  dimensionScores?: Record<string, number>;
  roundCount?: number;
  metThreshold?: boolean;
  metadata?: Record<string, unknown>;
  lessonSignal?: LessonSignal;
}

/** Trajectory report for a multi-generation agent task run. */
export interface AgentTaskTrajectory {
  taskName: string;
  totalGenerations: number;
  scoreHistory: number[];
  lessonsPerGeneration: number[];
  coldStartScore: number;
  finalScore: number;
  improvementDelta: number;
  metadata: Record<string, unknown>;
}

/**
 * A fixed code harness with a small evolved slot (AC-776).
 *
 * Function-slot evolution mode keeps the evolved unit small: the runner
 * carries only the slot in state and in the enriched prompt (so prompts stay
 * compact), while evaluation runs the assembled harness + slot. This avoids
 * the whole-program-bloat failure mode where carrying a large generated
 * artifact in `bestOutput` ballooned every prompt.
 *
 * Convention: the slot is prepended to the harness so the harness can
 * reference names the slot defines.
 */
export class FunctionSlot {
  constructor(public readonly harness: string) {}

  /** Return the full runnable program: slot prepended to harness. */
  assemble(slot: string): string {
    return `${slot}\n\n${this.harness}`;
  }
}

export type GenerateFn = (prompt: string, generation: number) => string | Promise<string>;
export type EvaluateFn = (
  output: string,
  generation: number,
) => AgentTaskGenerationEvaluation | Promise<AgentTaskGenerationEvaluation>;

function fixed2(value: number): string {
  return value.toFixed(2);
}

function formatMetric(value: number): string {
  // Mirror Python's `{v:g}` general format (trims trailing zeros).
  return Number.isInteger(value) ? String(value) : String(value);
}

/**
 * Extract a structured lesson from judge feedback for the playbook.
 *
 * When the evaluator supplies a {@link LessonSignal}, its actionable guidance
 * (hint, plateau flag, metrics) is rendered alongside the score and dimension
 * scores so the playbook carries move-level direction.
 */
export function accumulateLessons(
  judgeResult: AgentTaskResult,
  generation: number,
  signal?: LessonSignal,
): string {
  const parts: string[] = [`Generation ${generation} (score: ${fixed2(judgeResult.score)}):`];

  if (judgeResult.reasoning) {
    parts.push(`  Feedback: ${judgeResult.reasoning}`);
  }

  const dims = judgeResult.dimensionScores ?? {};

  const weak = Object.entries(dims).filter(([, s]) => s < 0.7);
  if (weak.length > 0) {
    weak.sort((a, b) => a[1] - b[1]);
    const strs = weak.map(([d, s]) => `${d} (${fixed2(s)})`);
    parts.push(`  Weak dimensions: ${strs.join(", ")}`);
  }

  const strong = Object.entries(dims).filter(([, s]) => s >= 0.8);
  if (strong.length > 0) {
    strong.sort((a, b) => b[1] - a[1]);
    const strs = strong.map(([d, s]) => `${d} (${fixed2(s)})`);
    parts.push(`  Strong dimensions: ${strs.join(", ")}`);
  }

  if (!judgeResult.reasoning && weak.length === 0) {
    parts.push(`  Score: ${fixed2(judgeResult.score)}`);
  }

  if (signal) {
    if (signal.hint) {
      parts.push(`  Hint: ${signal.hint}`);
    }
    if (signal.plateau) {
      parts.push(
        "  Plateau detected — a structurally different approach is needed; " +
          "incremental tweaks are not advancing the score.",
      );
    }
    if (signal.metrics && Object.keys(signal.metrics).length > 0) {
      const metricStrs = Object.entries(signal.metrics)
        .sort((a, b) => a[0].localeCompare(b[0]))
        .map(([k, v]) => `${k}=${formatMetric(v)}`);
      parts.push(`  Metrics: ${metricStrs.join(", ")}`);
    }
  }

  return parts.join("\n");
}

/**
 * Enrich a task prompt with cross-generation context.
 *
 * In function-slot mode (`harness` provided), the fixed harness is shown once
 * as stable context so the model knows the contract it writes the slot
 * against. The evolved slot itself is carried via `bestOutput`.
 */
export function buildEnrichedPrompt(args: {
  taskPrompt: string;
  playbook: string;
  generation: number;
  bestOutput: string;
  bestScore: number;
  harness?: string;
}): string {
  const playbook = compactPromptComponent("agent_task_playbook", args.playbook);
  const bestOutput = compactPromptComponent("agent_task_best_output", args.bestOutput);
  const sections: string[] = [args.taskPrompt];

  if (args.harness) {
    sections.push(
      "\n\n## Fixed Harness (do not modify; you write only the slot)\n" + `${args.harness}`,
    );
  }

  if (playbook) {
    sections.push(
      `\n\n## Accumulated Lessons (Generation ${args.generation})\n` +
        `Previous best score: ${fixed2(args.bestScore)}\n\n` +
        `${playbook}`,
    );
  }

  if (bestOutput) {
    sections.push(
      `\n\n## Best Previous Output (score ${fixed2(args.bestScore)})\n` + `${bestOutput}`,
    );
  }

  if (playbook || bestOutput) {
    sections.push(
      "\n\nUse the accumulated lessons and previous best output as context. " +
        "Produce an improved version that addresses the identified weaknesses.",
    );
  }

  return sections.join("\n");
}

/**
 * Island migration: seed lagging islands with the global champion.
 *
 * Each island below the best score adopts the champion's best output and
 * score (winners propagate), but keeps its own playbook so accumulated
 * lessons stay diverse. The champion island (and any tied) are unchanged.
 */
export function migrateStates(states: AgentTaskGenerationState[]): AgentTaskGenerationState[] {
  if (states.length === 0) {
    return states;
  }
  let champion = states[0];
  for (const s of states) {
    if (s.bestScore > champion.bestScore) {
      champion = s;
    }
  }
  return states.map((s) =>
    s.bestScore < champion.bestScore
      ? { ...s, bestOutput: champion.bestOutput, bestScore: champion.bestScore }
      : s,
  );
}

/** Multi-generation runner for AgentTask scenarios with lesson accumulation. */
export class AgentTaskEvolutionRunner {
  private readonly taskPrompt: string;
  private readonly generateFn: GenerateFn;
  private readonly evaluateFn: EvaluateFn;
  private readonly initialOutput: string;
  private readonly taskName: string;
  private readonly slot: FunctionSlot | undefined;

  constructor(args: {
    taskPrompt: string;
    generateFn: GenerateFn;
    evaluateFn: EvaluateFn;
    initialOutput?: string;
    taskName?: string;
    slot?: FunctionSlot;
  }) {
    this.taskPrompt = args.taskPrompt;
    this.generateFn = args.generateFn;
    this.evaluateFn = args.evaluateFn;
    this.initialOutput = args.initialOutput ?? "";
    this.taskName = args.taskName ?? "agent_task";
    this.slot = args.slot;
  }

  async runGeneration(state: AgentTaskGenerationState): Promise<AgentTaskGenerationState> {
    const prompt = buildEnrichedPrompt({
      taskPrompt: this.taskPrompt,
      playbook: state.playbook,
      generation: state.generation + 1,
      bestOutput: state.bestOutput,
      bestScore: state.bestScore,
      harness: this.slot?.harness,
    });

    let candidateOutput: string;
    if (state.generation === 0 && this.initialOutput) {
      candidateOutput = this.initialOutput;
    } else {
      candidateOutput = (await this.generateFn(prompt, state.generation)).trim();
      if (!candidateOutput) {
        candidateOutput = state.bestOutput;
      }
    }

    let evaluation: AgentTaskGenerationEvaluation;
    let evaluatedOutput: string;
    if (this.slot !== undefined) {
      // Function-slot mode: evaluate the assembled harness+slot, but carry
      // only the small slot forward (no whole-program bloat).
      evaluation = await this.evaluateFn(this.slot.assemble(candidateOutput), state.generation);
      evaluatedOutput = candidateOutput;
    } else {
      evaluation = await this.evaluateFn(candidateOutput, state.generation);
      evaluatedOutput = evaluation.output.trim() || candidateOutput;
    }

    const judgeResult: AgentTaskResult = {
      score: evaluation.score,
      reasoning: evaluation.reasoning,
      dimensionScores: evaluation.dimensionScores ?? {},
      internalRetries: 0,
    };

    const lesson = accumulateLessons(judgeResult, state.generation + 1, evaluation.lessonSignal);
    let newPlaybook = state.playbook;
    if (lesson) {
      newPlaybook = state.playbook ? `${state.playbook}\n${lesson}`.trim() : lesson;
    }

    let newBestOutput = state.bestOutput;
    let newBestScore = state.bestScore;
    if (!state.bestOutput || evaluation.score >= state.bestScore) {
      newBestOutput = evaluatedOutput;
      newBestScore = evaluation.score;
    }

    const metadata: Record<string, unknown> = { ...state.metadata };
    const generationPrompts = [...((metadata.generationPrompts as string[]) ?? []), prompt];
    const generationOutputs = [
      ...((metadata.generationOutputs as string[]) ?? []),
      evaluatedOutput,
    ];
    const generationRoundCounts = [
      ...((metadata.generationRoundCounts as number[]) ?? []),
      evaluation.roundCount ?? 1,
    ];
    const metThresholdHistory = [
      ...((metadata.metThresholdHistory as boolean[]) ?? []),
      evaluation.metThreshold ?? false,
    ];
    metadata.generationPrompts = generationPrompts;
    metadata.generationOutputs = generationOutputs;
    metadata.generationRoundCounts = generationRoundCounts;
    metadata.metThresholdHistory = metThresholdHistory;

    return {
      generation: state.generation + 1,
      bestOutput: newBestOutput,
      bestScore: newBestScore,
      playbook: newPlaybook,
      scoreHistory: [...state.scoreHistory, evaluation.score],
      lessonHistory: [...state.lessonHistory, lesson],
      metadata,
    };
  }

  async runWithState(numGenerations = 10): Promise<{
    trajectory: AgentTaskTrajectory;
    state: AgentTaskGenerationState;
  }> {
    let state: AgentTaskGenerationState = {
      generation: 0,
      bestOutput: "",
      bestScore: 0,
      playbook: "",
      scoreHistory: [],
      lessonHistory: [],
      metadata: {},
    };

    for (let i = 0; i < numGenerations; i += 1) {
      state = await this.runGeneration(state);
    }

    const sh = state.scoreHistory;
    const delta = sh.length > 0 ? Math.round((sh[sh.length - 1] - sh[0]) * 1e4) / 1e4 : 0;
    const trajectory: AgentTaskTrajectory = {
      taskName: this.taskName,
      totalGenerations: numGenerations,
      scoreHistory: sh,
      lessonsPerGeneration: state.lessonHistory.map((l) => (l ? 1 : 0)),
      coldStartScore: sh.length > 0 ? sh[0] : 0,
      finalScore: sh.length > 0 ? sh[sh.length - 1] : 0,
      improvementDelta: delta,
      metadata: {
        bestOutput: state.bestOutput,
        bestScore: state.bestScore,
        playbook: state.playbook,
        lessonHistory: state.lessonHistory,
        ...state.metadata,
      },
    };
    return { trajectory, state };
  }

  async run(numGenerations = 10): Promise<AgentTaskTrajectory> {
    return (await this.runWithState(numGenerations)).trajectory;
  }

  /**
   * Run `numIslands` parallel lineages, optionally migrating the global
   * champion into laggards every `migrateEvery` generations. Islands
   * preserve diversity (each keeps its own playbook/lineage) while migration
   * shares winners. `migrateEvery = 0` disables migration.
   */
  async runIslands(
    args: {
      numIslands?: number;
      numGenerations?: number;
      migrateEvery?: number;
    } = {},
  ): Promise<AgentTaskTrajectory> {
    const numIslands = args.numIslands ?? 4;
    const numGenerations = args.numGenerations ?? 10;
    const migrateEvery = args.migrateEvery ?? 0;

    if (numIslands < 1) {
      throw new RangeError(`numIslands must be >= 1, got ${numIslands}`);
    }

    let states: AgentTaskGenerationState[] = Array.from({ length: numIslands }, () => ({
      generation: 0,
      bestOutput: "",
      bestScore: 0,
      playbook: "",
      scoreHistory: [],
      lessonHistory: [],
      metadata: {},
    }));

    const bestPerGen: number[] = [];
    for (let gen = 0; gen < numGenerations; gen += 1) {
      // Islands are independent within a generation — run them concurrently.
      states = await Promise.all(states.map((s) => this.runGeneration(s)));
      bestPerGen.push(Math.max(...states.map((s) => s.bestScore)));
      if (migrateEvery && (gen + 1) % migrateEvery === 0) {
        states = migrateStates(states);
      }
    }

    let champion = states[0];
    for (const s of states) {
      if (s.bestScore > champion.bestScore) {
        champion = s;
      }
    }
    const delta =
      bestPerGen.length > 0
        ? Math.round((bestPerGen[bestPerGen.length - 1] - bestPerGen[0]) * 1e4) / 1e4
        : 0;
    return {
      taskName: this.taskName,
      totalGenerations: numGenerations,
      scoreHistory: bestPerGen,
      lessonsPerGeneration: bestPerGen.map(() => numIslands),
      coldStartScore: bestPerGen.length > 0 ? bestPerGen[0] : 0,
      finalScore: bestPerGen.length > 0 ? bestPerGen[bestPerGen.length - 1] : 0,
      improvementDelta: delta,
      metadata: {
        bestOutput: champion.bestOutput,
        bestScore: champion.bestScore,
        numIslands,
        playbook: champion.playbook,
      },
    };
  }
}
