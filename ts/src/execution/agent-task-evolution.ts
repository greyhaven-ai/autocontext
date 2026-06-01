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

/** Evaluation result for one cross-generation candidate. */
export interface AgentTaskGenerationEvaluation {
  output: string;
  score: number;
  reasoning: string;
  dimensionScores?: Record<string, number>;
  roundCount?: number;
  metThreshold?: boolean;
  metadata?: Record<string, unknown>;
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

export type GenerateFn = (prompt: string, generation: number) => string;
export type EvaluateFn = (output: string, generation: number) => AgentTaskGenerationEvaluation;

function fixed2(value: number): string {
  return value.toFixed(2);
}

/** Extract a structured lesson from judge feedback for the playbook. */
export function accumulateLessons(judgeResult: AgentTaskResult, generation: number): string {
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

  return parts.join("\n");
}

/** Enrich a task prompt with cross-generation context. */
export function buildEnrichedPrompt(args: {
  taskPrompt: string;
  playbook: string;
  generation: number;
  bestOutput: string;
  bestScore: number;
}): string {
  const playbook = compactPromptComponent("agent_task_playbook", args.playbook);
  const bestOutput = compactPromptComponent("agent_task_best_output", args.bestOutput);
  const sections: string[] = [args.taskPrompt];

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

/** Multi-generation runner for AgentTask scenarios with lesson accumulation. */
export class AgentTaskEvolutionRunner {
  private readonly taskPrompt: string;
  private readonly generateFn: GenerateFn;
  private readonly evaluateFn: EvaluateFn;
  private readonly initialOutput: string;
  private readonly taskName: string;

  constructor(args: {
    taskPrompt: string;
    generateFn: GenerateFn;
    evaluateFn: EvaluateFn;
    initialOutput?: string;
    taskName?: string;
  }) {
    this.taskPrompt = args.taskPrompt;
    this.generateFn = args.generateFn;
    this.evaluateFn = args.evaluateFn;
    this.initialOutput = args.initialOutput ?? "";
    this.taskName = args.taskName ?? "agent_task";
  }

  runGeneration(state: AgentTaskGenerationState): AgentTaskGenerationState {
    const prompt = buildEnrichedPrompt({
      taskPrompt: this.taskPrompt,
      playbook: state.playbook,
      generation: state.generation + 1,
      bestOutput: state.bestOutput,
      bestScore: state.bestScore,
    });

    let candidateOutput: string;
    if (state.generation === 0 && this.initialOutput) {
      candidateOutput = this.initialOutput;
    } else {
      candidateOutput = this.generateFn(prompt, state.generation).trim();
      if (!candidateOutput) {
        candidateOutput = state.bestOutput;
      }
    }

    const evaluation = this.evaluateFn(candidateOutput, state.generation);
    const evaluatedOutput = evaluation.output.trim() || candidateOutput;

    const judgeResult: AgentTaskResult = {
      score: evaluation.score,
      reasoning: evaluation.reasoning,
      dimensionScores: evaluation.dimensionScores ?? {},
      internalRetries: 0,
    };

    const lesson = accumulateLessons(judgeResult, state.generation + 1);
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

  runWithState(numGenerations = 10): {
    trajectory: AgentTaskTrajectory;
    state: AgentTaskGenerationState;
  } {
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
      state = this.runGeneration(state);
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

  run(numGenerations = 10): AgentTaskTrajectory {
    return this.runWithState(numGenerations).trajectory;
  }
}
