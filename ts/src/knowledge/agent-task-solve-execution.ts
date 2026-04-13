import type { AgentTaskInterface, ImprovementResult, LLMProvider } from "../types/index.js";
import { ImprovementLoop } from "../execution/improvement-loop.js";
import { createAgentTask } from "../scenarios/agent-task-factory.js";
import { AgentTaskSpecSchema, type AgentTaskSpec } from "../scenarios/agent-task-spec.js";
import { serializeSkillPackage } from "./package.js";
import { SkillPackage } from "./skill-package.js";

function readString(spec: Record<string, unknown>, ...keys: string[]): string | null {
  for (const key of keys) {
    const value = spec[key];
    if (typeof value === "string" && value.trim().length > 0) {
      return value.trim();
    }
  }
  return null;
}

function readStringArray(spec: Record<string, unknown>, ...keys: string[]): string[] | null {
  for (const key of keys) {
    const value = spec[key];
    if (Array.isArray(value) && value.every((entry) => typeof entry === "string")) {
      return value;
    }
  }
  return null;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function readRecordArray(
  spec: Record<string, unknown>,
  ...keys: string[]
): Array<Record<string, unknown>> | null {
  for (const key of keys) {
    const value = spec[key];
    if (Array.isArray(value) && value.every(isRecord)) {
      return value;
    }
  }
  return null;
}

function readNumber(spec: Record<string, unknown>, fallback: number, ...keys: string[]): number {
  for (const key of keys) {
    const value = spec[key];
    if (typeof value === "number" && Number.isFinite(value)) {
      return value;
    }
    if (typeof value === "string" && value.trim()) {
      const parsed = Number(value);
      if (!Number.isNaN(parsed)) {
        return parsed;
      }
    }
  }
  return fallback;
}

function humanizeScenarioName(name: string): string {
  return name.replace(/_/g, " ").replace(/\b\w/g, (char) => char.toUpperCase());
}

function buildAgentTaskLessons(
  result: {
    bestScore: number;
    totalRounds: number;
    terminationReason: string;
  },
  bestReasoning: string,
): string[] {
  const lessons = [
    `The best output reached ${result.bestScore.toFixed(4)} quality after ${result.totalRounds} rounds.`,
    `The loop stopped because '${result.terminationReason}'.`,
  ];
  if (bestReasoning.trim()) {
    lessons.push(bestReasoning.trim());
  }
  return lessons;
}

export function buildAgentTaskSolveSpec(
  rawSpec: Record<string, unknown>,
  fallbackRounds: number,
): AgentTaskSpec {
  const outputFormat = readString(rawSpec, "outputFormat", "output_format");
  return AgentTaskSpecSchema.parse({
    taskPrompt: readString(rawSpec, "taskPrompt", "task_prompt") ?? "",
    judgeRubric: readString(rawSpec, "judgeRubric", "judge_rubric", "rubric") ?? "Evaluate the response.",
    outputFormat: outputFormat === "json_schema" || outputFormat === "code" ? outputFormat : "free_text",
    judgeModel: readString(rawSpec, "judgeModel", "judge_model") ?? "",
    difficultyTiers: readRecordArray(rawSpec, "difficultyTiers", "difficulty_tiers"),
    referenceContext: readString(rawSpec, "referenceContext", "reference_context"),
    referenceSources: readStringArray(rawSpec, "referenceSources", "reference_sources"),
    requiredConcepts: readStringArray(rawSpec, "requiredConcepts", "required_concepts"),
    calibrationExamples: readRecordArray(rawSpec, "calibrationExamples", "calibration_examples"),
    contextPreparation: readString(rawSpec, "contextPreparation", "context_preparation"),
    requiredContextKeys: readStringArray(rawSpec, "requiredContextKeys", "required_context_keys"),
    maxRounds: readNumber(rawSpec, fallbackRounds, "maxRounds", "max_rounds"),
    qualityThreshold: readNumber(rawSpec, 0.9, "qualityThreshold", "quality_threshold"),
    revisionPrompt: readString(rawSpec, "revisionPrompt", "revision_prompt"),
    sampleInput: readString(rawSpec, "sampleInput", "sample_input"),
  });
}

export async function executeAgentTaskSolve(opts: {
  provider: LLMProvider;
  created: { name: string; spec: Record<string, unknown> };
  generations: number;
  deps?: {
    createTask?: (input: {
      spec: AgentTaskSpec;
      name: string;
      provider: LLMProvider;
    }) => AgentTaskInterface & { readonly name: string; readonly spec: AgentTaskSpec };
    createLoop?: (input: {
      task: AgentTaskInterface;
      maxRounds: number;
      qualityThreshold: number;
    }) => { run(input: Parameters<ImprovementLoop["run"]>[0]): Promise<ImprovementResult> };
  };
}): Promise<{ progress: number; result: Record<string, unknown> }> {
  const spec = buildAgentTaskSolveSpec(opts.created.spec, opts.generations);
  const task = (opts.deps?.createTask ?? createAgentTask)({
    spec,
    name: opts.created.name,
    provider: opts.provider,
  });
  const loop = (opts.deps?.createLoop ?? ((input) => new ImprovementLoop(input)))({
    task,
    maxRounds: spec.maxRounds,
    qualityThreshold: spec.qualityThreshold,
  });

  const initialState = task.prepareContext
    ? await task.prepareContext(task.initialState())
    : task.initialState();
  const contextErrors = task.validateContext
    ? task.validateContext(initialState)
    : [];
  if (contextErrors.length > 0) {
    throw new Error(`agent_task context preparation failed: ${contextErrors.join("; ")}`);
  }
  const initialOutput = await opts.provider.complete({
    systemPrompt: "You are a helpful assistant.",
    userPrompt: task.getTaskPrompt(initialState),
  });

  const result = await loop.run({
    initialOutput: initialOutput.text,
    state: initialState,
    referenceContext: spec.referenceContext ?? undefined,
    requiredConcepts: spec.requiredConcepts ?? undefined,
    calibrationExamples: spec.calibrationExamples ?? undefined,
  });
  const bestRound = result.rounds.find((round) => round.roundNumber === result.bestRound);
  const pkg = new SkillPackage({
    scenarioName: opts.created.name,
    displayName: humanizeScenarioName(opts.created.name),
    description: String(opts.created.spec.description ?? `Agent task: ${opts.created.name}`),
    playbook: [
      "## Improvement Summary",
      "",
      `- Best round: ${result.bestRound}`,
      `- Total rounds: ${result.totalRounds}`,
      `- Termination reason: ${result.terminationReason}`,
      `- Best score: ${result.bestScore.toFixed(4)}`,
      "",
      "## Best Output",
      "",
      result.bestOutput,
    ].join("\n"),
    lessons: buildAgentTaskLessons(result, bestRound?.reasoning ?? ""),
    bestStrategy: {
      family: "agent_task",
      best_round: result.bestRound,
      termination_reason: result.terminationReason,
    },
    bestScore: result.bestScore,
    bestElo: 1500,
    hints: "",
    metadata: {
      family: "agent_task",
      total_rounds: result.totalRounds,
      termination_reason: result.terminationReason,
      judge_failures: result.judgeFailures,
    },
    taskPrompt: spec.taskPrompt,
    judgeRubric: spec.judgeRubric,
    exampleOutputs: [{
      output: result.bestOutput,
      score: result.bestScore,
      reasoning: bestRound?.reasoning ?? "Best output from improvement loop.",
    }],
    outputFormat: spec.outputFormat,
    referenceContext: spec.referenceContext ?? null,
    contextPreparation: spec.contextPreparation ?? null,
    maxRounds: spec.maxRounds,
    qualityThreshold: spec.qualityThreshold,
  });
  return {
    progress: result.totalRounds,
    result: serializeSkillPackage(pkg),
  };
}
