/**
 * Training data export — strategy-level JSONL records aligned with Python (AC-366).
 * Mirrors Python's autocontext/training/export.py contract.
 */

import type { SQLiteStore, GenerationRow, RunRow } from "../storage/index.js";
import type { ArtifactStore } from "../knowledge/artifact-store.js";
import { PLAYBOOK_MARKERS } from "../knowledge/playbook.js";

export interface TrajectoryContextRecord {
  generation_index: number;
  best_score: number;
  gate_decision: string;
}

export interface TrainingRecord {
  run_id: string;
  scenario: string;
  generation_index: number;
  strategy: string;
  score: number;
  gate_decision: string;
  context: {
    playbook: string;
    hints: string;
    trajectory: TrajectoryContextRecord[];
  };
}

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

function extractMarkedSection(content: string, startMarker: string, endMarker: string): string {
  const start = content.indexOf(startMarker);
  const end = content.indexOf(endMarker);
  if (start === -1 || end === -1 || end <= start) return "";
  return content.slice(start + startMarker.length, end).trim();
}

function readHintsFromPlaybook(playbook: string): string {
  return extractMarkedSection(
    playbook,
    PLAYBOOK_MARKERS.HINTS_START,
    PLAYBOOK_MARKERS.HINTS_END,
  );
}

function resolveRuns(
  store: SQLiteStore,
  opts: ExportOpts,
): Array<Pick<RunRow, "run_id" | "scenario">> {
  if (opts.runId) {
    const run = store.getRun(opts.runId);
    return run ? [{ run_id: run.run_id, scenario: run.scenario }] : [];
  }
  if (opts.scenario) {
    return store.listAllRunsForScenario(opts.scenario).map((run) => ({
      run_id: run.run_id,
      scenario: run.scenario,
    }));
  }
  return [];
}

function buildTrajectorySnippet(
  generations: GenerationRow[],
  upToGenerationIndex: number,
): TrajectoryContextRecord[] {
  return generations
    .filter((gen) => gen.generation_index <= upToGenerationIndex)
    .map((gen) => ({
      generation_index: gen.generation_index,
      best_score: gen.best_score,
      gate_decision: gen.gate_decision,
    }));
}

function getCompetitorOutput(
  store: SQLiteStore,
  runId: string,
  generationIndex: number,
): string {
  const outputs = store.getAgentOutputs(runId, generationIndex);
  let competitorOutput = "";
  for (const output of outputs) {
    if (output.role === "competitor") {
      competitorOutput = output.content;
    }
  }
  return competitorOutput;
}

/**
 * Export training records from SQLite + artifacts.
 * Returns Python-compatible strategy and match records.
 */
export function exportTrainingData(
  store: SQLiteStore,
  artifacts: ArtifactStore,
  opts: ExportOpts,
): Array<TrainingRecord | MatchRecord> {
  const records: Array<TrainingRecord | MatchRecord> = [];
  const runs = resolveRuns(store, opts);

  for (const run of runs) {
    const playbook = artifacts.readPlaybook(run.scenario);
    const hints = readHintsFromPlaybook(playbook);
    const generations = store.getGenerations(run.run_id);

    for (const gen of generations) {
      if (gen.status !== "completed") continue;
      if (opts.keptOnly && gen.gate_decision !== "advance") continue;

      records.push({
        run_id: run.run_id,
        scenario: run.scenario,
        generation_index: gen.generation_index,
        strategy: getCompetitorOutput(store, run.run_id, gen.generation_index),
        score: gen.best_score,
        gate_decision: gen.gate_decision,
        context: {
          playbook,
          hints,
          trajectory: buildTrajectorySnippet(generations, gen.generation_index),
        },
      });

      if (!opts.includeMatches) continue;

      const matches = store.getMatchesForGeneration(run.run_id, gen.generation_index);
      for (const match of matches) {
        records.push({
          run_id: run.run_id,
          generation_index: gen.generation_index,
          seed: match.seed,
          score: match.score,
          passed_validation: Boolean(match.passed_validation),
          validation_errors: match.validation_errors,
        });
      }
    }
  }

  return records;
}
