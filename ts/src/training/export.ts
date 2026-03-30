/**
 * Training data export — Python-compatible JSONL format (AC-366).
 * Mirrors Python's autocontext/training/export.py + types.py.
 *
 * Field names use snake_case to match the Python contract so that
 * downstream training pipelines can consume TS-generated data without
 * field-name translation.
 */

import { extractDelimitedSection } from "../agents/roles.js";
import type { ArtifactStore } from "../knowledge/artifact-store.js";
import { resolveCustomAgentTask } from "../scenarios/custom-loader.js";
import { AGENT_TASK_REGISTRY, SCENARIO_REGISTRY } from "../scenarios/registry.js";
import type { SQLiteStore } from "../storage/index.js";

/**
 * One strategy-level training record — matches Python's TrainingRecord.
 * All fields are snake_case for cross-language compatibility.
 */
export interface TrainingRecord {
  run_id: string;
  scenario: string;
  generation_index: number;
  strategy: string;
  score: number;
  gate_decision: string;
  context: Record<string, unknown>;
}

/**
 * One match result — matches Python's MatchRecord.
 */
export interface MatchRecord {
  run_id: string;
  generation_index: number;
  seed: number;
  score: number;
  passed_validation: boolean;
  validation_errors: string;
}

export interface ExportOpts {
  runId?: string;
  scenario?: string;
  keptOnly?: boolean;
  includeMatches?: boolean;
  onProgress?: (progress: ExportProgress) => void;
}

export type TrainingExportRecord = TrainingRecord | MatchRecord;

export interface ExportProgress {
  phase: "start" | "run" | "generation";
  totalRuns: number;
  runIndex: number;
  runId: string;
  scenario: string;
  generationIndex?: number;
  recordsEmitted: number;
}

function extractHints(playbook: string): string {
  return (
    extractDelimitedSection(
      playbook,
      "<!-- COMPETITOR_HINTS_START -->",
      "<!-- COMPETITOR_HINTS_END -->",
    ) ?? ""
  );
}

function buildTrajectorySnippet(
  generations: Array<{
    generation_index: number;
    best_score: number;
    gate_decision: string;
  }>,
  upToIndex: number,
): Array<Record<string, unknown>> {
  return generations
    .filter((generation) => generation.generation_index <= upToIndex)
    .map((generation) => ({
      generation_index: generation.generation_index,
      best_score: generation.best_score,
      gate_decision: generation.gate_decision,
    }));
}

function resolvePromptContext(
  artifacts: ArtifactStore,
  scenarioName: string,
): Record<string, unknown> {
  const gameFactory = SCENARIO_REGISTRY[scenarioName];
  if (gameFactory) {
    const scenario = new gameFactory();
    return {
      scenarioRules: scenario.describeRules(),
      strategyInterface: scenario.describeStrategyInterface(),
      evaluationCriteria: scenario.describeEvaluationCriteria(),
    };
  }

  const builtinTaskFactory = AGENT_TASK_REGISTRY[scenarioName];
  if (builtinTaskFactory) {
    const task = new builtinTaskFactory();
    return {
      scenarioRules: task.describeTask(),
      strategyInterface: "Respond with output matching the task requirements.",
      evaluationCriteria: task.getRubric(),
    };
  }

  const customTask = resolveCustomAgentTask(artifacts.knowledgeRoot, scenarioName);
  if (customTask) {
    const outputFormat = customTask.spec.outputFormat === "json_schema"
      ? "Respond with JSON output matching the task requirements."
      : `Respond with ${customTask.spec.outputFormat} output matching the task requirements.`;
    return {
      scenarioRules: customTask.spec.taskPrompt,
      strategyInterface: outputFormat,
      evaluationCriteria: customTask.spec.judgeRubric,
    };
  }

  return {};
}

export function exportTrainingData(
  store: SQLiteStore,
  artifacts: ArtifactStore,
  opts: ExportOpts,
): TrainingExportRecord[] {
  const records: TrainingExportRecord[] = [];

  let runs: Array<{ run_id: string; scenario: string }>;
  if (opts.runId) {
    const run = store.getRun(opts.runId);
    if (!run) return [];
    runs = [{ run_id: run.run_id, scenario: run.scenario }];
  } else if (opts.scenario) {
    runs = store.listRunsForScenario(opts.scenario).map((r) => ({
      run_id: r.run_id,
      scenario: r.scenario,
    }));
  } else {
    return [];
  }

  opts.onProgress?.({
    phase: "start",
    totalRuns: runs.length,
    runIndex: 0,
    runId: "",
    scenario: opts.scenario ?? "",
    recordsEmitted: records.length,
  });

  for (const [runIndex, run] of runs.entries()) {
    opts.onProgress?.({
      phase: "run",
      totalRuns: runs.length,
      runIndex: runIndex + 1,
      runId: run.run_id,
      scenario: run.scenario,
      recordsEmitted: records.length,
    });

    const playbook = artifacts.readPlaybook(run.scenario);
    const hints = extractHints(playbook);
    const promptContext = resolvePromptContext(artifacts, run.scenario);
    const generations = store.getGenerations(run.run_id);

    for (const gen of generations) {
      if (opts.keptOnly && gen.gate_decision !== "advance") continue;

      const outputs = store.getAgentOutputs(run.run_id, gen.generation_index);
      const competitorOutput = outputs.find((o) => o.role === "competitor");
      const strategyStr = competitorOutput?.content ?? "";

      const record: TrainingRecord = {
        run_id: run.run_id,
        scenario: run.scenario,
        generation_index: gen.generation_index,
        strategy: strategyStr,
        score: gen.best_score,
        gate_decision: gen.gate_decision,
        context: {
          ...promptContext,
          playbook,
          hints,
          trajectory: buildTrajectorySnippet(generations, gen.generation_index),
        },
      };

      records.push(record);

      if (opts.includeMatches) {
        const matches = store.getMatchesForGeneration(run.run_id, gen.generation_index);
        records.push(
          ...matches.map((m) => ({
            run_id: run.run_id,
            generation_index: gen.generation_index,
            seed: m.seed,
            score: m.score,
            passed_validation: !!m.passed_validation,
            validation_errors: m.validation_errors,
          })),
        );
      }

      opts.onProgress?.({
        phase: "generation",
        totalRuns: runs.length,
        runIndex: runIndex + 1,
        runId: run.run_id,
        scenario: run.scenario,
        generationIndex: gen.generation_index,
        recordsEmitted: records.length,
      });
    }
  }

  return records;
}
