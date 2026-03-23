/**
 * Training data export — stream strategy-level records from SQLite (AC-366).
 * Mirrors Python's autocontext/training/export.py.
 */

import type { SQLiteStore, GenerationRow, MatchRow } from "../storage/index.js";

export interface TrainingRecord {
  runId: string;
  scenario: string;
  generationIndex: number;
  meanScore: number;
  bestScore: number;
  elo: number;
  gateDecision: string;
  strategy: Record<string, unknown> | null;
  rawCompetitorOutput: string;
  matches?: MatchExportRecord[];
}

export interface MatchExportRecord {
  seed: number;
  score: number;
  passedValidation: boolean;
  winner: string;
  strategyJson: string;
  replayJson: string;
}

export interface ExportOpts {
  runId?: string;
  scenario?: string;
  keptOnly?: boolean;
  includeMatches?: boolean;
}

/**
 * Export training records from SQLite.
 * Returns an array of TrainingRecord for each completed generation.
 */
export function exportTrainingData(
  store: SQLiteStore,
  opts: ExportOpts,
): TrainingRecord[] {
  const records: TrainingRecord[] = [];

  // Determine which runs to export
  let runs: Array<{ run_id: string; scenario: string }>;
  if (opts.runId) {
    const run = store.getRun(opts.runId);
    if (!run) return [];
    runs = [{ run_id: run.run_id, scenario: run.scenario }];
  } else if (opts.scenario) {
    runs = store.listRuns(1000, opts.scenario).map((r) => ({
      run_id: r.run_id,
      scenario: r.scenario,
    }));
  } else {
    return [];
  }

  for (const run of runs) {
    const generations = store.getGenerations(run.run_id);

    for (const gen of generations) {
      if (gen.status !== "completed") continue;
      if (opts.keptOnly && gen.gate_decision !== "advance") continue;

      // Get competitor output
      const outputs = store.getAgentOutputs(run.run_id, gen.generation_index);
      const competitorOutput = outputs.find((o) => o.role === "competitor");
      const rawText = competitorOutput?.content ?? "";

      let strategy: Record<string, unknown> | null = null;
      try {
        strategy = JSON.parse(rawText);
      } catch {
        strategy = null;
      }

      const record: TrainingRecord = {
        runId: run.run_id,
        scenario: run.scenario,
        generationIndex: gen.generation_index,
        meanScore: gen.mean_score,
        bestScore: gen.best_score,
        elo: gen.elo,
        gateDecision: gen.gate_decision,
        strategy,
        rawCompetitorOutput: rawText,
      };

      if (opts.includeMatches) {
        const matches = store.getMatchesForGeneration(run.run_id, gen.generation_index);
        record.matches = matches.map((m) => ({
          seed: m.seed,
          score: m.score,
          passedValidation: !!m.passed_validation,
          winner: m.winner,
          strategyJson: m.strategy_json,
          replayJson: m.replay_json,
        }));
      }

      records.push(record);
    }
  }

  return records;
}
