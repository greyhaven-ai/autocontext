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
}

export type TrainingExportRecord = TrainingRecord | MatchRecord;

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

  for (const run of runs) {
    const playbook = artifacts.readPlaybook(run.scenario);
    const hints = extractHints(playbook);
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
    }
  }

  return records;
}
